"""
Networks catalog (v0.15.0).

Admin-curated registry of libvirt bridges/networks. Two flags shape UX:

  * `user_visible` — `GET /api/networks` filters on this for non-admins,
    and the backend rejects VM-create requests from non-admins whose
    requested bridge isn't user_visible. Defaults to 0 (hidden).
  * `is_default`   — pre-selected in Quick Create for non-admins. At most
    one row may carry this; the service layer enforces by clearing
    everyone else's flag inside the same transaction whenever a row is
    set to default.

Lookup happens by `bridge_name` (the string we store in `vms.vlan` and
plug into `<source bridge='...'/>` in the libvirt XML). The display
`name` is purely cosmetic.

Side note on the model: the legacy `networks.subnet` and
`networks.available_on_nodes` columns from the original SQLAlchemy stub
are dropped at v0.15.0 — nothing in the codebase ever wrote them, and
the per-node "is this bridge actually here" question is answered live by
`vm_create.get_node_resources()`, not by a stored cache.
"""

import logging
import os
from datetime import datetime
from urllib.parse import urlparse

import pymysql
import pymysql.cursors

logger = logging.getLogger("wvc.network-service")


def _get_db():
    """Return a raw pymysql connection parsed from DATABASE_URL.

    autocommit=False so the create/update "clear other defaults + write
    this one" sequence runs in a single transaction.
    """
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
    """Serialise a network row for JSON output."""
    if isinstance(row.get("created_at"), datetime):
        row["created_at"] = row["created_at"].isoformat()
    row["user_visible"] = bool(row.get("user_visible"))
    row["is_default"]   = bool(row.get("is_default"))
    return row


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def list_networks(visible_only: bool = False) -> list[dict]:
    """Return all networks (or only user_visible=1).

    Adds a `vm_count` column for the Networks page table — the count is
    over all VMs whose `vms.vlan` string matches this bridge_name. Done
    in a single LEFT JOIN to avoid N+1 round-trips.
    """
    where_sql = "WHERE n.user_visible = 1" if visible_only else ""
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT n.id, n.name, n.bridge_name, n.vlan_id,
                       n.description, n.user_visible, n.is_default,
                       n.created_at,
                       COALESCE(vc.vm_count, 0) AS vm_count
                FROM networks n
                LEFT JOIN (
                    SELECT vlan, COUNT(*) AS vm_count
                    FROM vms WHERE vlan IS NOT NULL AND vlan != ''
                    GROUP BY vlan
                ) vc ON vc.vlan = n.bridge_name
                {where_sql}
                ORDER BY n.is_default DESC, n.vlan_id, n.name
                """
            )
            rows = cur.fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_network(network_id: int) -> dict | None:
    """Return a single network by id, or None."""
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, bridge_name, vlan_id, description,
                       user_visible, is_default, created_at
                FROM networks WHERE id = %s
                """,
                (network_id,),
            )
            row = cur.fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def get_network_by_bridge(bridge_name: str) -> dict | None:
    """Look up a network by its bridge_name (the libvirt identifier).

    Used by the backend VM-create gate to test whether a non-admin is
    allowed to use this bridge.
    """
    if not bridge_name:
        return None
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, bridge_name, vlan_id, description,
                       user_visible, is_default, created_at
                FROM networks WHERE bridge_name = %s
                """,
                (bridge_name,),
            )
            row = cur.fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def get_default_network() -> dict | None:
    """Return the default network (is_default=1) or None if no default set.

    Quick Create uses this to pre-select the dropdown for non-admins.
    Returns the only such row by service-layer invariant; if more than
    one row somehow carries the flag (manual SQL surgery), returns the
    one with the lowest id.
    """
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, bridge_name, vlan_id, description,
                       user_visible, is_default, created_at
                FROM networks WHERE is_default = 1
                ORDER BY id LIMIT 1
                """
            )
            row = cur.fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def is_bridge_user_visible(bridge_name: str) -> bool:
    """Cheap boolean check used by the create-VM gate.

    Returns True iff a networks row exists for this bridge_name with
    user_visible=1. False for unknown bridges or hidden ones — both
    cases mean "non-admin cannot use this bridge."
    """
    if not bridge_name:
        return False
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_visible FROM networks WHERE bridge_name = %s",
                (bridge_name,),
            )
            row = cur.fetchone()
        return bool(row and row["user_visible"])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Mutations (admin only — enforced at the API layer)
# ---------------------------------------------------------------------------

def _parse_vlan_id(raw) -> int | None:
    """Coerce vlan_id from the API. Accept int, numeric str, or null."""
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def create_network(name: str, bridge_name: str, vlan_id=None,
                   description: str | None = None,
                   user_visible: bool = False,
                   is_default: bool = False) -> dict:
    """Insert a new network. Returns {"ok": True, "id": <id>} or {"error": ...}.

    If is_default=True, clears the flag on every other row first so the
    "exactly one default" invariant holds.
    """
    name        = (name or "").strip()
    bridge_name = (bridge_name or "").strip()
    description = (description or "").strip() or None
    vlan_id     = _parse_vlan_id(vlan_id)
    user_visible = 1 if user_visible else 0
    is_default   = 1 if is_default else 0

    if not name:
        return {"error": "Name is required"}
    if not bridge_name:
        return {"error": "Bridge name is required"}

    conn = _get_db()
    try:
        with conn.cursor() as cur:
            if is_default:
                # Clear every other default in the same transaction so
                # we never have a window with two defaults visible.
                cur.execute("UPDATE networks SET is_default = 0 WHERE is_default = 1")
            cur.execute(
                """
                INSERT INTO networks
                    (name, bridge_name, vlan_id, description,
                     user_visible, is_default, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (name, bridge_name, vlan_id, description,
                 user_visible, is_default, datetime.utcnow()),
            )
            new_id = cur.lastrowid
            conn.commit()
        logger.info(
            "Created network id=%s name=%r bridge=%r user_visible=%d is_default=%d",
            new_id, name, bridge_name, user_visible, is_default,
        )
        return {"ok": True, "id": new_id}
    except pymysql.err.IntegrityError as e:
        conn.rollback()
        if e.args and e.args[0] == 1062 and "uq_bridge_name" in str(e):
            return {"error": f"A network for bridge {bridge_name!r} already exists"}
        return {"error": f"Database error: {e}"}
    finally:
        conn.close()


def update_network(network_id: int, fields: dict) -> dict:
    """Update one or more fields. is_default=True clears other defaults."""
    existing = get_network(network_id)
    if not existing:
        return {"error": "Network not found"}

    sets: list[str] = []
    args: list = []
    setting_default = False
    for col in ("name", "bridge_name", "vlan_id", "description",
                "user_visible", "is_default"):
        if col not in fields:
            continue
        val = fields[col]
        if col in ("user_visible", "is_default"):
            val = 1 if val else 0
            if col == "is_default" and val == 1:
                setting_default = True
        elif col == "vlan_id":
            val = _parse_vlan_id(val)
        elif col in ("name", "bridge_name", "description"):
            if isinstance(val, str):
                val = val.strip() or None
            if col in ("name", "bridge_name") and not val:
                return {"error": f"{col} cannot be empty"}
        sets.append(f"{col} = %s")
        args.append(val)

    if not sets:
        return {"error": "No updatable fields supplied"}

    conn = _get_db()
    try:
        with conn.cursor() as cur:
            if setting_default:
                # Clear other defaults BEFORE writing this row's flag so
                # we never momentarily have two defaults set.
                cur.execute(
                    "UPDATE networks SET is_default = 0 "
                    "WHERE is_default = 1 AND id != %s",
                    (network_id,),
                )
            args.append(network_id)
            cur.execute(
                f"UPDATE networks SET {', '.join(sets)} WHERE id = %s",
                args,
            )
            conn.commit()
        logger.info("Updated network id=%s fields=%s",
                    network_id, list(fields.keys()))
        return {"ok": True}
    except pymysql.err.IntegrityError as e:
        conn.rollback()
        if e.args and e.args[0] == 1062 and "uq_bridge_name" in str(e):
            return {"error": "Another network already has that bridge name"}
        return {"error": f"Database error: {e}"}
    finally:
        conn.close()


def delete_network(network_id: int) -> dict:
    """Delete a network. Rejects if any VM currently uses the bridge.

    We don't enforce a DB foreign key from vms → networks (the bridge
    name string is the link), so this is a check-then-delete done in one
    transaction — close enough for an admin-only operation that runs at
    UI speed.
    """
    existing = get_network(network_id)
    if not existing:
        return {"error": "Network not found"}

    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM vms WHERE vlan = %s",
                (existing["bridge_name"],),
            )
            row = cur.fetchone()
            if row and row["n"]:
                return {
                    "error": (
                        f"{row['n']} VM(s) still use bridge "
                        f"{existing['bridge_name']!r}. Move or delete "
                        "them first."
                    )
                }
            cur.execute("DELETE FROM networks WHERE id = %s", (network_id,))
            conn.commit()
        logger.info("Deleted network id=%s name=%r bridge=%r",
                    network_id, existing["name"], existing["bridge_name"])
        return {"ok": True}
    finally:
        conn.close()
