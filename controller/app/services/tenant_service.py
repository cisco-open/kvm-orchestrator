# SPDX-License-Identifier: Apache-2.0
#
# Copyright [2026] [Cisco Systems, Inc. and its affiliates]
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tenant service — raw SQL, no ORM.

A tenant is a unit of multi-tenancy: a named group (typically bound to an AD
group DN) that owns a set of VMs and grants its members visibility/operation
rights on those VMs.

Two tenant kinds:
  * **Built-in** (`is_builtin = 1`) — seeded at migration:
      - id=1 `SHARED`   — visible to every authenticated user (VMs).
      - id=3 `ODaaSE`   — visible only to `vms.owner_user_id`. The
        defining property is owner-binding rather than group membership.
        Historically two tenants ("Personal VM" id=2 and "ODaaSE" id=3)
        occupied this space; migration 0004 (v0.21.0) merged them into
        the single id=3 tenant, keeping the name "ODaaSE" and adopting
        Personal VM's owner-only visibility. `PERSONAL_TENANT_ID` still
        exists as a code identifier and points at id=3.
      - id=4 `DEFAULT`  — the shared placement fallback pool.
        Visible to every authenticated user (NODES) and used as the
        second-pass placement pool when the caller's tenant has no
        dedicated node with capacity.
    Built-in tenants cannot be deleted or renamed and their `ad_group_dn` is
    NULL.
  * **Real** — admin-created, bound to an AD group DN. Membership is
    populated/refreshed by `group_service.sync_user_groups_from_ad()`.

Visibility for a VM (v0.10+, id renumbered in v0.21):
    admin
    OR vm.tenant_id = SHARED                                  (id=1)
    OR (vm.tenant_id = PERSONAL AND vm.owner_user_id = me)    (id=3, was id=2 pre-v0.21)
    OR vm.tenant_id IN (tenants the user is a member of)

Visibility for a node (v0.20+):
    admin
    OR node has DEFAULT in node_tenants                       (id=4)
    OR node has any tenant the user is a member of

`vm.tenant_id IS NULL` is admin-only (an "untagged" state — discovered VMs
that haven't yet been classified, or VMs an admin deliberately hid from
non-admins). Similarly, a node with zero rows in `node_tenants` is
admin-only.
"""

import logging
import os
from datetime import datetime
from urllib.parse import urlparse

import pymysql
import pymysql.cursors

logger = logging.getLogger("wvc.tenant-service")

# Built-in tenant ids — seeded by migrations.
SHARED_TENANT_ID   = 1   # everyone sees these VMs
PERSONAL_TENANT_ID = 3   # owner_user_id sees these (and admins) — named "ODaaSE" in DB since v0.21 merge (was id=2 "Personal VM" pre-v0.21)
DEFAULT_TENANT_ID  = 4   # everyone sees these NODES; placement fallback pool (v0.20+)
BUILTIN_TENANT_IDS = (
    SHARED_TENANT_ID,
    PERSONAL_TENANT_ID,
    DEFAULT_TENANT_ID,
)


def _get_db():
    url = urlparse(os.environ.get("DATABASE_URL", ""))
    return pymysql.connect(
        host=url.hostname,
        port=url.port or 3306,
        user=url.username,
        password=url.password,
        database=url.path.lstrip("/"),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def _row_to_dict(row: dict) -> dict:
    """Serialise a tenant row for JSON output."""
    if isinstance(row.get("created_at"), datetime):
        row["created_at"] = row["created_at"].isoformat()
    row["is_builtin"] = bool(row.get("is_builtin"))
    return row


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def list_tenants() -> list[dict]:
    """Return all tenants, with a vm_count column."""
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT t.id, t.name, t.ad_group_dn, t.description,
                       t.is_builtin, t.created_at,
                       COALESCE(vc.vm_count, 0) AS vm_count,
                       COALESCE(uc.member_count, 0) AS member_count
                FROM tenants t
                LEFT JOIN (
                    SELECT tenant_id, COUNT(*) AS vm_count
                    FROM vms WHERE tenant_id IS NOT NULL
                    GROUP BY tenant_id
                ) vc ON vc.tenant_id = t.id
                LEFT JOIN (
                    SELECT tenant_id, COUNT(*) AS member_count
                    FROM user_tenants
                    GROUP BY tenant_id
                ) uc ON uc.tenant_id = t.id
                ORDER BY t.is_builtin DESC, t.name
                """
            )
            rows = cur.fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_tenant(tenant_id: int) -> dict | None:
    """Return a single tenant by id, or None."""
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, ad_group_dn, description, is_builtin, created_at "
                "FROM tenants WHERE id = %s",
                (tenant_id,),
            )
            row = cur.fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def get_user_tenant_ids(user_id: int) -> list[int]:
    """Return the list of tenant ids the user is a member of.

    SHARED is *not* included here — visibility checks should always add
    SHARED_TENANT_ID separately. Keeping it implicit means a user who is in
    zero AD groups doesn't get a fake user_tenants row.
    """
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tenant_id FROM user_tenants WHERE user_id = %s",
                (user_id,),
            )
            return [r["tenant_id"] for r in cur.fetchall()]
    finally:
        conn.close()


def get_user_visibility(user_id: int) -> dict:
    """Return the visibility profile for a user in one round-trip.

    Result:
        {
          "is_admin":        bool,          # admins see everything
          "tenant_ids":      list[int],     # for VMs: SHARED + AD-mapped
          "node_tenant_ids": list[int],     # for nodes: DEFAULT + AD-mapped
        }

    The caller composes the WHERE clause:
        admin:      no filter
        non-admin (VMs):   v.tenant_id IN (tenant_ids)
                           OR (v.tenant_id = PERSONAL AND v.owner_user_id = user_id)
        non-admin (nodes): n.id IN (SELECT node_id FROM node_tenants
                                    WHERE tenant_id IN (node_tenant_ids))

    PERSONAL is excluded from `tenant_ids` because it requires the
    per-row owner check; folding it into the IN list would leak every
    user's personal VMs to every other user.

    `tenant_ids` and `node_tenant_ids` always contain at least their
    respective "visible to all" ID (SHARED for VMs, DEFAULT for nodes)
    so callers can safely use `IN (...)` without an empty-list SQL
    syntax error.
    """
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT is_admin FROM users WHERE id = %s AND is_active = 1",
                (user_id,),
            )
            row = cur.fetchone()
            is_admin = bool(row and row.get("is_admin"))

            cur.execute(
                "SELECT tenant_id FROM user_tenants WHERE user_id = %s",
                (user_id,),
            )
            mapped = [r["tenant_id"] for r in cur.fetchall()]
    finally:
        conn.close()

    # AD-mapped tenants, excluding built-ins. Built-ins never map to an
    # AD group (their `ad_group_dn` is NULL) so this is doubly defensive
    # against a bad `user_tenants` insert.
    ad_mapped = [t for t in mapped if t not in BUILTIN_TENANT_IDS]

    # VM visibility: SHARED + AD-mapped.
    tenant_ids = sorted({SHARED_TENANT_ID, *ad_mapped})

    # Node visibility: DEFAULT + AD-mapped.
    # DEFAULT-tagged nodes are visible to every authenticated user
    # (fleet-wide placement fallback pool). PERSONAL/ODaaSE (id=3) is
    # an owner-only VM tenant since the v0.21 merge — no node
    # visibility semantics.
    node_tenant_ids = sorted({DEFAULT_TENANT_ID, *ad_mapped})

    return {
        "is_admin": is_admin,
        "tenant_ids": tenant_ids,
        "node_tenant_ids": node_tenant_ids,
    }


# ---------------------------------------------------------------------------
# Mutations (admin only — enforced at the API layer)
# ---------------------------------------------------------------------------

def create_tenant(name: str, ad_group_dn: str | None,
                  description: str | None) -> dict:
    """Create a new tenant. Returns dict with `id` on success or `error`."""
    name        = (name or "").strip()
    ad_group_dn = (ad_group_dn or "").strip() or None
    description = (description or "").strip() or None

    if not name:
        return {"error": "Name is required"}
    if name.upper() == "SHARED":
        return {"error": "'SHARED' is reserved for the built-in tenant"}
    if ad_group_dn is None:
        return {"error": "ad_group_dn is required for non-built-in tenants"}

    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tenants (name, ad_group_dn, description, is_builtin) "
                "VALUES (%s, %s, %s, 0)",
                (name, ad_group_dn, description),
            )
            new_id = cur.lastrowid
            conn.commit()
        logger.info("Created tenant id=%s name=%r ad_group_dn=%r",
                    new_id, name, ad_group_dn)
        return {"ok": True, "id": new_id}
    except pymysql.err.IntegrityError as e:
        conn.rollback()
        # 1062 = duplicate key
        if e.args and e.args[0] == 1062:
            if "uq_tenant_name" in str(e):
                return {"error": f"A tenant named {name!r} already exists"}
            if "uq_tenant_ad_group" in str(e):
                return {"error": "Another tenant is already bound to that AD group DN"}
        return {"error": f"Database error: {e}"}
    finally:
        conn.close()


def update_tenant(tenant_id: int, fields: dict) -> dict:
    """Update name / ad_group_dn / description. Built-in tenants reject."""
    existing = get_tenant(tenant_id)
    if not existing:
        return {"error": "Tenant not found"}
    if existing["is_builtin"]:
        return {"error": "Built-in tenants cannot be modified"}

    sets: list[str] = []
    args: list = []
    for col in ("name", "ad_group_dn", "description"):
        if col in fields:
            val = fields[col]
            if isinstance(val, str):
                val = val.strip() or None
            if col == "name" and not val:
                return {"error": "Name cannot be empty"}
            if col == "name" and val and val.upper() == "SHARED":
                return {"error": "'SHARED' is reserved for the built-in tenant"}
            sets.append(f"{col} = %s")
            args.append(val)

    if not sets:
        return {"error": "No updatable fields supplied"}

    args.append(tenant_id)
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE tenants SET {', '.join(sets)} WHERE id = %s",
                args,
            )
            conn.commit()
        logger.info("Updated tenant id=%s fields=%s",
                    tenant_id, list(fields.keys()))
        return {"ok": True}
    except pymysql.err.IntegrityError as e:
        conn.rollback()
        if e.args and e.args[0] == 1062:
            if "uq_tenant_name" in str(e):
                return {"error": "Another tenant already has that name"}
            if "uq_tenant_ad_group" in str(e):
                return {"error": "Another tenant is already bound to that AD group DN"}
        return {"error": f"Database error: {e}"}
    finally:
        conn.close()


def delete_tenant(tenant_id: int) -> dict:
    """Delete a tenant. Rejects built-in tenants and tenants with VMs attached.

    `user_tenants` rows cascade automatically via FK ON DELETE CASCADE.
    `vms.tenant_id` would be SET NULL by the FK, but we surface the error
    instead — leaving orphan personal-tagged VMs unexpectedly visible to
    fewer people is rarely what the admin wanted.
    """
    existing = get_tenant(tenant_id)
    if not existing:
        return {"error": "Tenant not found"}
    if existing["is_builtin"]:
        return {"error": "Built-in tenants cannot be deleted"}

    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM vms WHERE tenant_id = %s",
                (tenant_id,),
            )
            row = cur.fetchone()
            if row and row["n"]:
                return {
                    "error": (
                        f"Tenant has {row['n']} VM(s) attached. "
                        "Re-tag or delete them first."
                    )
                }
            cur.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))
            conn.commit()
        logger.info("Deleted tenant id=%s name=%r", tenant_id, existing["name"])
        return {"ok": True}
    finally:
        conn.close()
