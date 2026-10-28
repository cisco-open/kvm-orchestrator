# SPDX-License-Identifier: Apache-2.0
#
# Copyright 2026 Cisco Systems, Inc. and its affiliates
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

"""Node service — raw SQL, no ORM."""

import io
import logging
import os
import re
from urllib.parse import urlparse

import paramiko
import pymysql
import pymysql.cursors

logger = logging.getLogger("wvc.node-service")

# Simple pattern to detect bare IPv4 / IPv6 addresses
_IP_RE = re.compile(
    r"^(\d{1,3}\.){3}\d{1,3}$"   # IPv4
    r"|"
    r"^[0-9a-fA-F:]+$"            # IPv6 (simplified)
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


def _load_key(key_str: str) -> paramiko.PKey:
    for cls in (paramiko.Ed25519Key, paramiko.RSAKey,
                paramiko.ECDSAKey, paramiko.DSSKey):
        try:
            return cls.from_private_key(io.StringIO(key_str))
        except Exception:
            pass
    raise ValueError("Unsupported private key format")


def _resolve_hostname(ip_address: str, ssh_host: str | None,
                      ssh_port: int, ssh_user: str, key_id: int) -> str:
    """
    SSH to the host and return its hostname.
    Runs: hostname -f 2>/dev/null || hostname
    Raises ValueError if the key is not found or SSH fails.
    """
    private_key_str = get_ssh_key_private(key_id)
    if not private_key_str:
        raise ValueError(f"SSH key id={key_id} not found")

    pkey = _load_key(private_key_str)
    host = ssh_host or ip_address
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(hostname=host, port=ssh_port, username=ssh_user,
                       pkey=pkey, timeout=10,
                       look_for_keys=False, allow_agent=False)
        _, stdout, _ = client.exec_command(
            "hostname -f 2>/dev/null || hostname", timeout=10
        )
        name = stdout.read().decode(errors="replace").strip()
        if not name:
            raise ValueError("hostname command returned empty output")
        logger.info("Resolved hostname for %s → %r", ip_address, name)
        return name
    finally:
        client.close()


# ---------------------------------------------------------------------------
# SSH keys
# ---------------------------------------------------------------------------

def list_ssh_keys() -> list:
    """Return all SSH keys (name + id only, no private key material)."""
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, created_at FROM ssh_keys ORDER BY name")
            return cur.fetchall()
    finally:
        conn.close()


def add_ssh_key(name: str, private_key: str) -> dict:
    """Store a named SSH private key. Returns the new row."""
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ssh_keys (name, private_key) VALUES (%s, %s)",
                (name, private_key),
            )
            conn.commit()
            key_id = cur.lastrowid
            cur.execute("SELECT id, name, created_at FROM ssh_keys WHERE id = %s", (key_id,))
            return cur.fetchone()
    finally:
        conn.close()


def get_ssh_key_private(key_id: int) -> str | None:
    """Return the raw private key for a given key ID (used internally by agent deploy)."""
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT private_key FROM ssh_keys WHERE id = %s", (key_id,))
            row = cur.fetchone()
            return row["private_key"] if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

# Fields selected for every node row. Kept in a module constant so list/get
# stay in sync — deviating a column in one but not the other silently breaks
# the frontend.
_NODE_COLS = """
    n.id, n.hostname, n.ip_address, n.ssh_host, n.ssh_port,
    n.ssh_user, n.ssh_key_id, k.name AS ssh_key_name,
    n.cpu_model, n.port, n.status, n.total_vcpus, n.total_ram_mb,
    n.total_disk_gb, n.available_vcpus, n.available_ram_mb,
    n.available_disk_gb, n.agent_version, n.last_heartbeat_at,
    n.ha_enabled, n.ha_priority, n.created_at, n.updated_at,
    n.kvm_version, n.qemu_version, n.libvirt_version, n.kernel_version,
    n.schedulable
"""


def _fetch_node_tenants(cur, node_ids: list[int]) -> dict[int, list[dict]]:
    """Given a list of node ids, return {node_id: [{id, name}, ...]}.

    Single JOIN query, ordered so the returned lists are stable
    (name-sorted) for a nicer UI.
    """
    if not node_ids:
        return {}
    placeholders = ",".join(["%s"] * len(node_ids))
    cur.execute(
        f"""
        SELECT nt.node_id, t.id AS tenant_id, t.name AS tenant_name
        FROM node_tenants nt
        JOIN tenants t ON t.id = nt.tenant_id
        WHERE nt.node_id IN ({placeholders})
        ORDER BY nt.node_id, t.name
        """,
        tuple(node_ids),
    )
    out: dict[int, list[dict]] = {nid: [] for nid in node_ids}
    for r in cur.fetchall():
        out[r["node_id"]].append({"id": r["tenant_id"], "name": r["tenant_name"]})
    return out


def _attach_tenants(cur, rows: list[dict]) -> list[dict]:
    """Mutate `rows` in place to add a `tenants` list to each. Returns `rows`."""
    ids = [r["id"] for r in rows]
    tmap = _fetch_node_tenants(cur, ids)
    for r in rows:
        r["tenants"] = tmap.get(r["id"], [])
    return rows


def list_nodes(user_id: int | None = None) -> list:
    """Return nodes visible to the given user.

    * `user_id=None` → all nodes (internal callers, tests). No auth.
    * user is admin → all nodes.
    * otherwise → only nodes tagged with a tenant in the user's
      `node_tenant_ids` (which always includes DEFAULT since v0.20).
      Nodes with zero tenant tags are hidden from non-admins.

    Each returned row includes a `tenants: [{id, name}]` list.
    """
    # Import here to avoid a circular import (tenant_service is imported by
    # a bunch of other services that in turn import this module).
    from app.services.tenant_service import get_user_visibility

    where_sql = ""
    where_args: tuple = ()
    if user_id is not None:
        vis = get_user_visibility(user_id)
        if not vis["is_admin"]:
            ids = vis["node_tenant_ids"]
            # node_tenant_ids always contains DEFAULT so the IN list is never empty.
            placeholders = ",".join(["%s"] * len(ids))
            where_sql = f"""
                WHERE n.id IN (
                    SELECT DISTINCT node_id FROM node_tenants
                    WHERE tenant_id IN ({placeholders})
                )
            """
            where_args = tuple(ids)

    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {_NODE_COLS}
                FROM nodes n
                LEFT JOIN ssh_keys k ON k.id = n.ssh_key_id
                {where_sql}
                ORDER BY n.hostname
                """,
                where_args,
            )
            rows = cur.fetchall()
            return _attach_tenants(cur, rows)
    finally:
        conn.close()


def can_user_see_node(user_id: int, node_id: int) -> bool:
    """Cheap single-row visibility check. Admin → True.

    Used by API 404 gates and vm_create tenant enforcement.
    """
    from app.services.tenant_service import get_user_visibility

    vis = get_user_visibility(user_id)
    if vis["is_admin"]:
        return True

    conn = _get_db()
    try:
        with conn.cursor() as cur:
            ids = vis["node_tenant_ids"]
            placeholders = ",".join(["%s"] * len(ids))
            cur.execute(
                f"""
                SELECT 1 FROM node_tenants
                WHERE node_id = %s AND tenant_id IN ({placeholders})
                LIMIT 1
                """,
                (node_id, *ids),
            )
            return cur.fetchone() is not None
    finally:
        conn.close()


def get_node(node_id: int, user_id: int | None = None) -> dict | None:
    """Return a single node by ID with its tenant list.

    If `user_id` is given and the user cannot see the node, returns None
    (the API layer should translate that to 404).
    """
    if user_id is not None and not can_user_see_node(user_id, node_id):
        return None

    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {_NODE_COLS}
                FROM nodes n
                LEFT JOIN ssh_keys k ON k.id = n.ssh_key_id
                WHERE n.id = %s
                """,
                (node_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return _attach_tenants(cur, [row])[0]
    finally:
        conn.close()


def _set_node_tenants_locked(cur, node_id: int, tenant_ids: list[int]) -> None:
    """Replace the node's tenant set. Caller owns the transaction.

    Silently drops duplicate tenant_ids in the input.
    """
    cur.execute("DELETE FROM node_tenants WHERE node_id = %s", (node_id,))
    unique = sorted(set(tenant_ids))
    if unique:
        cur.executemany(
            "INSERT INTO node_tenants (node_id, tenant_id) VALUES (%s, %s)",
            [(node_id, tid) for tid in unique],
        )


def set_node_tenants(node_id: int, tenant_ids: list[int]) -> dict | None:
    """Replace the node's tenant set. Admin-only (enforced at API layer).

    Returns the updated node dict, or None if the node doesn't exist.
    Validates that every tenant_id refers to a real tenant; unknown IDs
    raise ValueError.
    """
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM nodes WHERE id = %s", (node_id,))
            if not cur.fetchone():
                return None

            unique = sorted(set(tenant_ids or []))
            if unique:
                placeholders = ",".join(["%s"] * len(unique))
                cur.execute(
                    f"SELECT id FROM tenants WHERE id IN ({placeholders})",
                    tuple(unique),
                )
                found = {r["id"] for r in cur.fetchall()}
                missing = [t for t in unique if t not in found]
                if missing:
                    raise ValueError(f"Unknown tenant ids: {missing}")

            _set_node_tenants_locked(cur, node_id, unique)
        conn.commit()
        logger.info("Node %d tenants set to %s", node_id, unique)
        return get_node(node_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_node_schedulable(node_id: int, schedulable: bool) -> dict | None:
    """Cordon (schedulable=False) or uncordon (True) a node. Admin-only
    (enforced at API layer).

    A cordoned node accepts no new VMs — the placement algorithm and the
    migrate-target UI exclude it. Existing VMs are left running; the node
    stays online. Returns the updated node dict, or None if not found.
    """
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM nodes WHERE id = %s", (node_id,))
            if not cur.fetchone():
                return None
            cur.execute(
                "UPDATE nodes SET schedulable = %s WHERE id = %s",
                (1 if schedulable else 0, node_id),
            )
        conn.commit()
        logger.info("Node %d schedulable set to %s", node_id, bool(schedulable))
        return get_node(node_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def add_node(hostname: str, ip_address: str, ssh_user: str, ssh_key_id: int,
             ssh_host: str | None = None, ssh_port: int = 22,
             tenant_ids: list[int] | None = None) -> dict:
    """
    Register a new node. Returns the created row (including tenant list).

    If hostname is blank or is a bare IP address, SSHes to the host and
    populates hostname from the remote `hostname -f` output.

    `tenant_ids` — optional list of tenant IDs to tag the node with. If
    omitted or empty, the node has NO tenant tags and will only be
    visible to admins. Callers who want the pre-v0.18 behaviour of
    "visible to everyone" should pass `[DEFAULT_TENANT_ID]` (v0.20+).
    """
    # Auto-resolve hostname when not supplied or when the caller passed an IP
    if not hostname or _IP_RE.match(hostname):
        hostname = _resolve_hostname(ip_address, ssh_host, ssh_port,
                                     ssh_user, ssh_key_id)

    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO nodes
                    (hostname, ip_address, ssh_host, ssh_port, ssh_user, ssh_key_id)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (hostname, ip_address, ssh_host or ip_address, ssh_port, ssh_user, ssh_key_id))
            new_id = cur.lastrowid
            if tenant_ids:
                _set_node_tenants_locked(cur, new_id, tenant_ids)
        conn.commit()
        return get_node(new_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_node(node_id: int) -> bool:
    """Delete a node and all VMs assigned to it. Returns True if the node existed."""
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            # Verify the node exists before touching anything
            cur.execute("SELECT id FROM nodes WHERE id = %s", (node_id,))
            if not cur.fetchone():
                return False

            # Remove VMs first (FK: vms.node_id → nodes.id)
            cur.execute("DELETE FROM vms WHERE node_id = %s", (node_id,))
            vm_count = cur.rowcount

            # Remove the node
            cur.execute("DELETE FROM nodes WHERE id = %s", (node_id,))
        conn.commit()

        import logging
        logging.getLogger("wvc.node-service").info(
            "Node %d deleted; removed %d VM record(s)", node_id, vm_count
        )
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_ha_events(node_id: int | None = None, limit: int = 100) -> list:
    """Return recent HA events (audit feed), newest first, joined with
    node + VM names for display. Optionally filter to one node."""
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            where = ""
            args: tuple = ()
            if node_id is not None:
                where = "WHERE e.node_id = %s OR e.source_node_id = %s"
                args = (node_id, node_id)
            cur.execute(
                f"""
                SELECT e.id, e.timestamp, e.node_id, e.vm_id,
                       e.source_node_id, e.target_node_id,
                       e.`trigger`, e.result, e.detail,
                       n.hostname AS node_hostname,
                       sn.hostname AS source_hostname,
                       tn.hostname AS target_hostname,
                       v.name AS vm_name
                FROM ha_events e
                LEFT JOIN nodes n  ON n.id  = e.node_id
                LEFT JOIN nodes sn ON sn.id = e.source_node_id
                LEFT JOIN nodes tn ON tn.id = e.target_node_id
                LEFT JOIN vms   v  ON v.id  = e.vm_id
                {where}
                ORDER BY e.id DESC
                LIMIT %s
                """,
                (*args, limit),
            )
            rows = cur.fetchall()
            # `detail` is JSON in MySQL → pymysql returns it as a str; parse.
            import json as _json
            from datetime import datetime as _dt
            for r in rows:
                if isinstance(r.get("detail"), str):
                    try:
                        r["detail"] = _json.loads(r["detail"])
                    except Exception:
                        pass
                # Return an ISO-8601 UTC string so JS `new Date()` parses it
                # reliably. Stored timestamps are naive UTC (utcnow()); append
                # 'Z' to mark them as such.
                ts = r.get("timestamp")
                if isinstance(ts, _dt):
                    r["timestamp"] = ts.isoformat() + "Z"
            return rows
    finally:
        conn.close()
