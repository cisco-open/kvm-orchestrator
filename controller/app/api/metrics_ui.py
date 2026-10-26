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

"""In-UI metrics endpoints (admin-only, DB-backed) — for the Dashboard and the
Metrics page. Distinct from the root /metrics Prometheus scrape endpoint.
"""

import os
from urllib.parse import urlparse

import pymysql
import pymysql.cursors
from flask import Blueprint, jsonify, request

from ..middleware.auth import admin_required

bp = Blueprint("metrics_ui", __name__)


def _db():
    url = urlparse(os.environ.get("DATABASE_URL", ""))
    return pymysql.connect(
        host=url.hostname, port=url.port or 3306, user=url.username,
        password=url.password, database=url.path.lstrip("/"),
        cursorclass=pymysql.cursors.DictCursor,
    )


@bp.route("/summary", methods=["GET"])
@admin_required
def summary():
    """Cluster + per-node capacity, VM counts by state and OS."""
    conn = _db()
    try:
        with conn.cursor() as cur:
            # Cluster capacity (online nodes).
            cur.execute(
                """
                SELECT
                  COALESCE(SUM(total_vcpus),0) tv, COALESCE(SUM(available_vcpus),0) av,
                  COALESCE(SUM(total_ram_mb),0) tr, COALESCE(SUM(available_ram_mb),0) ar,
                  COALESCE(SUM(total_disk_gb),0) td, COALESCE(SUM(available_disk_gb),0) ad,
                  COUNT(*) online_nodes
                FROM nodes WHERE status='online'
                """
            )
            c = cur.fetchone() or {}

            # Allocated vCPU/RAM (sum of running VMs) — for overcommit AND for
            # the authoritative "used" figures. NOTE: the node poller sets
            # available_vcpus = total host cores and never decrements it (vCPU is
            # overcommittable, so the host OS has no meaningful "free vCPU"
            # reading). So cluster/per-node vCPU "used" must be derived from the
            # vCPUs ALLOCATED to running/paused VMs, not from total-available.
            cur.execute(
                "SELECT COALESCE(SUM(vcpus),0) vc, COALESCE(SUM(ram_mb),0) rm "
                "FROM vms WHERE state IN ('running','paused')"
            )
            alloc = cur.fetchone() or {}

            # Per-node allocated vCPU (and RAM) from running/paused VMs, so each
            # node's utilisation reflects real placement.
            cur.execute(
                "SELECT node_id, COALESCE(SUM(vcpus),0) vc, COALESCE(SUM(ram_mb),0) rm "
                "FROM vms WHERE state IN ('running','paused') GROUP BY node_id"
            )
            per_node_alloc = {r["node_id"]: r for r in cur.fetchall()}

            # Per-node utilisation.
            cur.execute(
                """
                SELECT id, hostname, status, cpu_model,
                       total_vcpus, available_vcpus,
                       total_ram_mb, available_ram_mb,
                       total_disk_gb, available_disk_gb
                FROM nodes ORDER BY hostname
                """
            )
            nodes = cur.fetchall()

            cur.execute(
                "SELECT COALESCE(state,'unknown') s, COUNT(*) c FROM vms GROUP BY state"
            )
            vms_by_state = {r["s"]: r["c"] for r in cur.fetchall()}

            cur.execute(
                "SELECT COALESCE(os_family,'unknown') o, COUNT(*) c FROM vms GROUP BY os_family"
            )
            vms_by_os = {r["o"]: r["c"] for r in cur.fetchall()}

            cur.execute("SELECT COUNT(*) c FROM vms")
            total_vms = (cur.fetchone() or {}).get("c", 0)

        tv, av = c.get("tv", 0), c.get("av", 0)
        tr, ar = c.get("tr", 0), c.get("ar", 0)
        td, ad = c.get("td", 0), c.get("ad", 0)
        # vCPU "used" is what running/paused VMs have allocated (see note above).
        vcpus_used = alloc.get("vc", 0)
        return jsonify({
            "cluster": {
                "online_nodes": c.get("online_nodes", 0),
                "vcpus_total": tv,
                "vcpus_available": max(tv - vcpus_used, 0),
                "vcpus_used": vcpus_used,
                "ram_mb_total": tr, "ram_mb_available": ar, "ram_mb_used": tr - ar,
                "disk_gb_total": td, "disk_gb_available": ad, "disk_gb_used": td - ad,
                "vcpus_allocated": alloc.get("vc", 0),
                "ram_mb_allocated": alloc.get("rm", 0),
                "vcpu_overcommit": round(alloc.get("vc", 0) / tv, 2) if tv else 0,
            },
            "nodes": [{
                "id": n["id"], "hostname": n["hostname"], "status": n["status"],
                "cpu_model": n["cpu_model"],
                "vcpus_total": n["total_vcpus"],
                # Per-node vCPU used = vcpus of running/paused VMs on this node;
                # available = total - used (see cluster note on why not the
                # poller's available_vcpus).
                "vcpus_used": per_node_alloc.get(n["id"], {}).get("vc", 0),
                "vcpus_available": max(
                    n["total_vcpus"] - per_node_alloc.get(n["id"], {}).get("vc", 0), 0),
                "ram_mb_total": n["total_ram_mb"], "ram_mb_available": n["available_ram_mb"],
                "disk_gb_total": n["total_disk_gb"], "disk_gb_available": n["available_disk_gb"],
            } for n in nodes],
            "vms_total": total_vms,
            "vms_by_state": vms_by_state,
            "vms_by_os": vms_by_os,
        }), 200
    finally:
        conn.close()


@bp.route("/vms-created", methods=["GET"])
@admin_required
def vms_created():
    """Cumulative VM count over time, by OS family + total.

    Rises on create, FALLS on delete (from the vm_lifecycle_events ledger).
    Each day's value is the running total of (creates - deletes) up to and
    including that day. Query: ?days=N (default 90, cap 365).
    """
    try:
        days = int(request.args.get("days", "90"))
    except ValueError:
        days = 90
    days = max(1, min(days, 365))

    from datetime import date, timedelta
    today = date.today()
    start = today - timedelta(days=days - 1)
    families = ["linux", "windows", "custom", "unknown"]

    conn = _db()
    try:
        with conn.cursor() as cur:
            # Starting level BEFORE the window: net (creates - deletes) per OS
            # for everything up to the day before `start`.
            cur.execute(
                """
                SELECT COALESCE(os_family,'unknown') os,
                       SUM(CASE WHEN event='create' THEN 1
                                WHEN event='delete' THEN -1 ELSE 0 END) net
                FROM vm_lifecycle_events
                WHERE ts < %s
                GROUP BY os_family
                """,
                (start.isoformat(),),
            )
            base = {r["os"]: int(r["net"] or 0) for r in cur.fetchall()}

            # Daily net delta within the window, per OS.
            cur.execute(
                """
                SELECT DATE(ts) d, COALESCE(os_family,'unknown') os,
                       SUM(CASE WHEN event='create' THEN 1
                                WHEN event='delete' THEN -1 ELSE 0 END) net
                FROM vm_lifecycle_events
                WHERE ts >= %s
                GROUP BY DATE(ts), os_family
                ORDER BY d
                """,
                (start.isoformat(),),
            )
            deltas: dict = {}
            for r in cur.fetchall():
                d = r["d"]
                key = d.isoformat() if hasattr(d, "isoformat") else str(d)
                deltas.setdefault(key, {})[r["os"]] = int(r["net"] or 0)
    finally:
        conn.close()

    # Running cumulative per family (normalise any family not in `families`
    # into 'custom'/'unknown' is already done by GROUP BY os_family values).
    running = {fam: base.get(fam, 0) for fam in families}
    # include any base OS not in families (e.g. legacy odd values) under unknown
    for k, v in base.items():
        if k not in families:
            running["unknown"] += v

    series = []
    for i in range(days):
        day = (start + timedelta(days=i)).isoformat()
        day_delta = deltas.get(day, {})
        for fam in families:
            running[fam] += day_delta.get(fam, 0)
        for k, v in day_delta.items():
            if k not in families:
                running["unknown"] += v
        rec = {"date": day}
        total = 0
        for fam in families:
            rec[fam] = max(0, running[fam])  # guard against negative from partial backfill
            total += rec[fam]
        rec["total"] = total
        series.append(rec)

    return jsonify({"days": days, "families": families, "series": series}), 200
