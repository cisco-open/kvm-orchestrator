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

"""HA control endpoints (admin-only).

Global HA arm status + the runtime kill-switch live here. Node-scoped
force-failover and readmit live on the nodes blueprint. Recent HA events
(audit feed) are also exposed here.
"""

from flask import Blueprint, jsonify, request

from ..middleware.auth import admin_required
from ..middleware.mcp import current_user_id
from ..services import ha_service, ha_engine, node_service

bp = Blueprint("ha", __name__)


def _current_username() -> str | None:
    uid = current_user_id()
    if uid is None:
        return None
    try:
        import os, pymysql, pymysql.cursors
        from urllib.parse import urlparse
        url = urlparse(os.environ.get("DATABASE_URL", ""))
        conn = pymysql.connect(
            host=url.hostname, port=url.port or 3306, user=url.username,
            password=url.password, database=url.path.lstrip("/"),
            cursorclass=pymysql.cursors.DictCursor,
        )
        with conn.cursor() as cur:
            cur.execute("SELECT username FROM users WHERE id = %s", (uid,))
            row = cur.fetchone()
        conn.close()
        return row["username"] if row else str(uid)
    except Exception:
        return str(uid)


@bp.route("/status", methods=["GET"])
@admin_required
def status():
    """Current HA arm status for the UI banner."""
    return jsonify(ha_service.ha_status()), 200


@bp.route("/runtime", methods=["POST"])
@admin_required
def set_runtime():
    """Flip the runtime kill-switch. Body: {enabled: bool, reason?: str}.

    Takes effect within one celery-beat cycle (~15s) on the worker — no
    restart. Effective arming still also requires HA_ENABLED (env).
    """
    data = request.get_json() or {}
    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        return jsonify({"error": "enabled must be a boolean"}), 400
    reason = (data.get("reason") or "").strip() or None
    result = ha_service.set_runtime_enabled(
        enabled, by_user=_current_username(), reason=reason)
    if "error" in result:
        return jsonify(result), 500
    return jsonify(result), 200


@bp.route("/events", methods=["GET"])
@admin_required
def events():
    """Recent HA events (audit feed). Query: ?limit=N (default 100)."""
    try:
        limit = int(request.args.get("limit", "100"))
    except ValueError:
        limit = 100
    limit = max(1, min(limit, 1000))
    return jsonify({"events": node_service.list_ha_events(limit=limit)}), 200


@bp.route("/affected-vms", methods=["GET"])
@admin_required
def affected_vms():
    """VMs on failed (fenced/offline) nodes — the recovery worklist for admins.

    Returns, per affected node, the VMs that need migrating. Flags whether each
    VM has stored libvirt_xml (required to rebuild it on a survivor).
    """
    import os
    import pymysql
    import pymysql.cursors
    from urllib.parse import urlparse

    url = urlparse(os.environ.get("DATABASE_URL", ""))
    conn = pymysql.connect(
        host=url.hostname, port=url.port or 3306, user=url.username,
        password=url.password, database=url.path.lstrip("/"),
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT v.id, v.name, v.state, v.node_id,
                       v.vcpus, v.ram_mb, v.disk_gb, v.ip,
                       v.tenant_id, v.priority,
                       v.last_running_at,
                       -- How long BEFORE the node went dark was the VM last seen
                       -- running? Measured against the node's last heartbeat
                       -- (the failure moment), NOT 'now' — so the verdict does
                       -- not decay as the outage drags on.
                       TIMESTAMPDIFF(SECOND, v.last_running_at,
                                     COALESCE(n.last_heartbeat_at, UTC_TIMESTAMP()))
                           AS running_before_fail_s,
                       (v.libvirt_xml IS NOT NULL AND v.libvirt_xml <> '') AS has_xml,
                       n.hostname AS node_hostname, n.status AS node_status,
                       n.cpu_model AS node_cpu_model
                FROM vms v
                JOIN nodes n ON n.id = v.node_id
                WHERE n.status IN ('fenced', 'failed', 'offline')
                ORDER BY n.hostname, FIELD(v.priority,'critical','priority','default'), v.name
                """
            )
            rows = cur.fetchall()

            # Online, schedulable candidate targets (with cpu_model for arch match).
            cur.execute(
                """
                SELECT id, hostname, cpu_model,
                       available_vcpus, available_ram_mb, available_disk_gb
                FROM nodes
                WHERE status = 'online' AND schedulable = 1
                """
            )
            candidates = list(cur.fetchall())

        # A VM "was on" if vm_sync last observed it running/paused close to when
        # the node went dark — i.e. last_running_at is within a small window of
        # the node's last heartbeat. This is measured at the FAILURE MOMENT and
        # therefore does not decay while the node stays down.
        import os as _os
        threshold = int(_os.environ.get("HA_FAILOVER_THRESHOLD", "120"))
        # Allow a generous margin: sync interval + threshold. If the VM was seen
        # running within this many seconds *before* the last heartbeat, it was up.
        fresh_window = max(threshold * 2, 300)

        # Group by node for the UI.
        nodes: dict = {}
        for r in rows:
            r["has_xml"] = bool(r["has_xml"])
            key = r["node_id"]
            if key not in nodes:
                failed_cpu = (r["node_cpu_model"] or "").strip()
                # Per-node compatible target list: exact cpu_model match with the
                # failed node = safe to start there (green). Others may still work
                # if newer, but exact match is the guaranteed-compatible signal.
                compat = []
                for c in candidates:
                    if c["id"] == r["node_id"]:
                        continue
                    c_cpu = (c["cpu_model"] or "").strip()
                    compat.append({
                        "id": c["id"], "hostname": c["hostname"],
                        "cpu_model": c["cpu_model"],
                        "arch_match": bool(failed_cpu) and c_cpu == failed_cpu,
                        "available_vcpus": c["available_vcpus"],
                        "available_ram_mb": c["available_ram_mb"],
                    })
                # Sort: arch-matched first, then by free RAM desc.
                compat.sort(key=lambda x: (not x["arch_match"], -(x["available_ram_mb"] or 0)))
                nodes[key] = {
                    "node_id": r["node_id"],
                    "hostname": r["node_hostname"],
                    "status": r["node_status"],
                    "cpu_model": r["node_cpu_model"],
                    "targets": compat,
                    "vms": [],
                }
            # Derive previous state: was the VM running when the node went dark?
            # running_before_fail_s = last_running_at relative to the node's last
            # heartbeat. Small positive (seen running shortly before) or negative
            # (seen running at/after that point) => it was up. Large positive =>
            # it had already been shut down well before the failure.
            gap = r.get("running_before_fail_s")
            was_running = gap is not None and gap <= fresh_window
            was_state = "running" if was_running else "off"
            nodes[key]["vms"].append({
                "id": r["id"], "name": r["name"], "state": r["state"],
                "was_state": was_state,
                "vcpus": r["vcpus"], "ram_mb": r["ram_mb"], "disk_gb": r["disk_gb"],
                "ip": r["ip"], "priority": r["priority"], "has_xml": r["has_xml"],
            })
        return jsonify({"nodes": list(nodes.values())}), 200
    finally:
        conn.close()


@bp.route("/summary", methods=["GET"])
@admin_required
def summary():
    """Dashboard summary: HA event counts (24h) by result + node status counts.
    Lightweight — for the dashboard cards."""
    import os
    import pymysql
    import pymysql.cursors
    from urllib.parse import urlparse

    url = urlparse(os.environ.get("DATABASE_URL", ""))
    conn = pymysql.connect(
        host=url.hostname, port=url.port or 3306, user=url.username,
        password=url.password, database=url.path.lstrip("/"),
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT result, COUNT(*) c FROM ha_events "
                "WHERE timestamp > UTC_TIMESTAMP() - INTERVAL 24 HOUR "
                "GROUP BY result"
            )
            by_result = {r["result"]: r["c"] for r in cur.fetchall()}
            cur.execute(
                "SELECT COUNT(*) c FROM ha_events "
                "WHERE timestamp > UTC_TIMESTAMP() - INTERVAL 24 HOUR"
            )
            total_24h = cur.fetchone()["c"]
            cur.execute("SELECT status, COUNT(*) c FROM nodes GROUP BY status")
            node_status = {r["status"]: r["c"] for r in cur.fetchall()}
            # Nodes needing admin attention right now.
            cur.execute(
                "SELECT COUNT(*) c FROM nodes WHERE status IN ('failed','fenced')")
            nodes_attention = cur.fetchone()["c"]

        # Latest event timestamp for a 'last activity' hint.
        latest = node_service.list_ha_events(limit=1)
        last_event = latest[0] if latest else None

        return jsonify({
            "events_24h": total_24h,
            "events_24h_by_result": by_result,
            "node_status": node_status,
            "nodes_needing_attention": nodes_attention,
            "last_event": last_event,
        }), 200
    finally:
        conn.close()
