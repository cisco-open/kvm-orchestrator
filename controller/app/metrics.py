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

"""Prometheus metrics for the kvm-orchestrator controller.

Design
------
- **Multiprocess mode.** Gunicorn runs with `--workers 4`, each worker a separate
  OS process. Without multiprocess mode, each worker would maintain its own
  in-memory counter values and Prometheus scrapes (which land on one worker
  chosen by the socket) would see nonsensically low counts and constant
  apparent "resets". `prometheus_client.multiprocess` solves this by having
  every worker write counter+histogram samples to shard files in
  `PROMETHEUS_MULTIPROC_DIR`. On scrape, `MultiProcessCollector` sweeps the
  dir and sums shards to give a coherent view.

  `PROMETHEUS_MULTIPROC_DIR` **must be set before any Counter/Histogram is
  instantiated** — the prometheus_client library reads the env var at
  metric-object construction time and picks an in-memory backend if it's
  unset. The Docker `ENV` line + `gunicorn.conf.py`'s `on_starting` hook
  belt-and-braces this.

- **DB-backed gauges via custom Collector.** For "current state" metrics
  (VMs by state, nodes by status, cluster capacity) we don't want per-worker
  writes — we want the DB's authoritative answer at scrape time. The
  `_StateCollector` class runs a small set of `GROUP BY` queries on every
  scrape (whichever worker gets the scrape does the queries). Cheap.

Instrumentation surface
-----------------------
- HTTP RED: `install(app)` wires `before_request`/`after_request` hooks that
  populate `wvc_http_requests_total{method,endpoint,status}` and
  `wvc_http_request_duration_seconds{method,endpoint}`.
- VM operations: `@track_vm_action`, `@track_vm_create`, `@track_vm_migrate`,
  `@track_operation("edit"|"delete")` decorators wrap the service-layer
  functions and bump `wvc_vm_operations_total{operation,result}`; migrate
  also observes `wvc_vm_migration_duration_seconds{mode}`.
- Background loops: `vm_sync` and `node_monitor` call the counters +
  histograms directly (`vm_sync_iterations_total`,
  `vm_sync_duration_seconds`, `node_monitor_iterations_total`,
  `node_poll_duration_seconds`).
- LDAP: `ldap_service` bumps `wvc_ldap_binds_total{kind,result}` directly.

Cardinality budget
------------------
All labels are bounded:
- HTTP endpoints ≈ 40, methods ≈ 5, status codes ≈ 10 → ~2000 series.
- VM operations ≈ 15 (create.{blank,clone,existing,quick}, power.{start,
  stop,force-off,reboot,suspend,resume}, edit, delete, migrate) × {ok,err}.
- LDAP kinds = 3, result = 2 → 6 series.
- State gauges: VM states ≤ 8, node statuses ≤ 4.
Total well under 5000 series — no cardinality concerns.

Endpoint
--------
`GET /metrics` (root, not `/api/*`) — unauthenticated. Protect at the network
layer (Prometheus scrapers should be reachable only from monitoring subnets).
"""

import logging
import os

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)
from prometheus_client.core import GaugeMetricFamily

logger = logging.getLogger("wvc.metrics")


# ---------------------------------------------------------------------------
# Multiprocess directory bootstrap
# ---------------------------------------------------------------------------

# Belt-and-braces: the Dockerfile sets PROMETHEUS_MULTIPROC_DIR via ENV and
# gunicorn.conf.py's on_starting hook wipes+recreates it. This block only
# helps out-of-container dev runs (`python -m flask run`) — in production
# these lines are effectively no-ops.
_MULTIPROC_DIR = os.environ.setdefault(
    "PROMETHEUS_MULTIPROC_DIR", "/tmp/prometheus_multiproc"
)
os.makedirs(_MULTIPROC_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# HTTP RED
# ---------------------------------------------------------------------------

http_requests_total = Counter(
    "wvc_http_requests_total",
    "Total HTTP requests handled by the controller API",
    ["method", "endpoint", "status"],
)

http_request_duration_seconds = Histogram(
    "wvc_http_request_duration_seconds",
    "HTTP request latency, seconds",
    ["method", "endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)


# ---------------------------------------------------------------------------
# VM operations
# ---------------------------------------------------------------------------

vm_operations_total = Counter(
    "wvc_vm_operations_total",
    "VM operations performed by the controller",
    ["operation", "result"],
)

vm_created_total = Counter(
    "wvc_vm_created_total",
    "VMs created, labelled by OS family and create mode",
    ["os_family", "mode"],  # os: linux|windows|custom  mode: blank|clone|existing|quick
)

vm_migration_duration_seconds = Histogram(
    "wvc_vm_migration_duration_seconds",
    "VM migration duration, seconds (successful migrations only)",
    ["mode"],  # "live" | "offline"
    buckets=(1, 5, 10, 30, 60, 120, 300, 600, 1800, 3600),
)


# ---------------------------------------------------------------------------
# Background loops
# ---------------------------------------------------------------------------

vm_sync_iterations_total = Counter(
    "wvc_vm_sync_iterations_total",
    "vm_sync loop iterations",
    ["result"],  # ok | error
)

vm_sync_duration_seconds = Histogram(
    "wvc_vm_sync_duration_seconds",
    "Duration of one full vm_sync iteration (all online nodes), seconds",
    buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)

node_monitor_iterations_total = Counter(
    "wvc_node_monitor_iterations_total",
    "node_monitor loop iterations",
    ["result"],
)

node_poll_duration_seconds = Histogram(
    "wvc_node_poll_duration_seconds",
    "Duration of one node SSH poll, seconds",
    ["result"],  # ok | fail
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)


# ---------------------------------------------------------------------------
# LDAP
# ---------------------------------------------------------------------------

ldap_binds_total = Counter(
    "wvc_ldap_binds_total",
    "LDAP bind attempts",
    ["kind", "result"],  # kind: service | user | group_query; result: ok | fail
)


# ---------------------------------------------------------------------------
# Auth / login events
# ---------------------------------------------------------------------------

logins_total = Counter(
    "wvc_logins_total",
    "User login attempts against POST /api/auth/login",
    ["result"],  # success | invalid_credentials | missing_fields
)


# ---------------------------------------------------------------------------
# MCP impersonation auth (self-service MCP sidecar → wvc REST API)
# ---------------------------------------------------------------------------

mcp_auth_total = Counter(
    "wvc_mcp_auth_total",
    "MCP impersonation auth outcomes at /api/vms/* endpoints",
    ["result"],  # ok | bad_secret | unknown_or_disabled
)


# ---------------------------------------------------------------------------
# Info gauge (build version)
# ---------------------------------------------------------------------------

build_info = Gauge(
    "wvc_build_info",
    "Controller build info (value is always 1; version is a label)",
    ["version"],
    multiprocess_mode="max",
)


def set_build_info(version: str) -> None:
    """Publish the running controller version as a labelled gauge."""
    build_info.labels(version=version).set(1)


# ---------------------------------------------------------------------------
# DB-backed collector — reads authoritative state on every scrape
# ---------------------------------------------------------------------------

class _StateCollector:
    """Emits VM/node counts and cluster capacity from the DB at scrape time.

    Runs inside whichever worker got the scrape. Queries are cheap: three
    GROUP BY-style queries against small tables (`vms`, `nodes`, `tenants`).
    """

    def collect(self):
        # Local import to avoid module-import cycle (services import metrics).
        try:
            from .services.vm_service import _get_db
        except Exception as exc:
            logger.warning("StateCollector: cannot import _get_db: %s", exc)
            return

        vms_gauge = GaugeMetricFamily(
            "wvc_vms",
            "VMs known to kvm-orchestrator, grouped by state",
            labels=["state"],
        )
        vms_os_gauge = GaugeMetricFamily(
            "wvc_vms_by_os",
            "VMs known to kvm-orchestrator, grouped by state and OS family",
            labels=["state", "os_family"],
        )
        nodes_gauge = GaugeMetricFamily(
            "wvc_nodes",
            "Nodes registered with kvm-orchestrator, grouped by status",
            labels=["status"],
        )
        tenants_gauge = GaugeMetricFamily(
            "wvc_tenants_total",
            "Total tenants (excluding built-ins SHARED and PERSONAL)",
        )
        users_gauge = GaugeMetricFamily(
            "wvc_users_total",
            "Total users known to the controller",
        )
        cap_vcpus = GaugeMetricFamily(
            "wvc_cluster_vcpus_total",
            "Sum of total vCPUs across online nodes",
        )
        cap_vcpus_free = GaugeMetricFamily(
            "wvc_cluster_vcpus_available",
            "Sum of available vCPUs across online nodes",
        )
        cap_ram = GaugeMetricFamily(
            "wvc_cluster_ram_mb_total",
            "Sum of total RAM (MB) across online nodes",
        )
        cap_ram_free = GaugeMetricFamily(
            "wvc_cluster_ram_mb_available",
            "Sum of available RAM (MB) across online nodes",
        )
        cap_disk = GaugeMetricFamily(
            "wvc_cluster_disk_gb_total",
            "Sum of total disk (GB) across online nodes",
        )
        cap_disk_free = GaugeMetricFamily(
            "wvc_cluster_disk_gb_available",
            "Sum of available disk (GB) across online nodes",
        )

        try:
            conn = _get_db()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COALESCE(state, 'unknown') AS state, COUNT(*) AS c "
                        "FROM vms GROUP BY state"
                    )
                    for row in cur.fetchall():
                        vms_gauge.add_metric([row["state"]], row["c"])

                    cur.execute(
                        "SELECT COALESCE(state,'unknown') AS state, "
                        "COALESCE(os_family,'unknown') AS os, COUNT(*) AS c "
                        "FROM vms GROUP BY state, os_family"
                    )
                    for row in cur.fetchall():
                        vms_os_gauge.add_metric([row["state"], row["os"]], row["c"])

                    cur.execute(
                        "SELECT COALESCE(status, 'unknown') AS status, COUNT(*) AS c "
                        "FROM nodes GROUP BY status"
                    )
                    for row in cur.fetchall():
                        nodes_gauge.add_metric([row["status"]], row["c"])

                    cur.execute("SELECT COUNT(*) AS c FROM tenants WHERE is_builtin = 0")
                    tenants_gauge.add_metric([], (cur.fetchone() or {}).get("c", 0))

                    cur.execute("SELECT COUNT(*) AS c FROM users")
                    users_gauge.add_metric([], (cur.fetchone() or {}).get("c", 0))

                    cur.execute(
                        """
                        SELECT
                          COALESCE(SUM(total_vcpus), 0)      AS tv,
                          COALESCE(SUM(available_vcpus), 0)  AS av,
                          COALESCE(SUM(total_ram_mb), 0)     AS tr,
                          COALESCE(SUM(available_ram_mb), 0) AS ar,
                          COALESCE(SUM(total_disk_gb), 0)    AS td,
                          COALESCE(SUM(available_disk_gb), 0) AS ad
                        FROM nodes
                        WHERE status = 'online'
                        """
                    )
                    row = cur.fetchone() or {}
                    # vCPU free must be derived from allocation: the node poller
                    # sets available_vcpus = total host cores and never decrements
                    # it (vCPU is overcommittable). Real "used" = vcpus of
                    # running/paused VMs; free = total - used.
                    cur.execute(
                        "SELECT COALESCE(SUM(vcpus),0) AS vc FROM vms "
                        "WHERE state IN ('running','paused')"
                    )
                    vcpus_used = (cur.fetchone() or {}).get("vc", 0)
                    _tv = row.get("tv", 0)
                    cap_vcpus.add_metric([], _tv)
                    cap_vcpus_free.add_metric([], max(_tv - vcpus_used, 0))
                    cap_ram.add_metric([], row.get("tr", 0))
                    cap_ram_free.add_metric([], row.get("ar", 0))
                    cap_disk.add_metric([], row.get("td", 0))
                    cap_disk_free.add_metric([], row.get("ad", 0))
            finally:
                conn.close()
        except Exception as exc:
            # Never let a metrics collector break the scrape — log and emit
            # whatever partial results we assembled.
            logger.warning("StateCollector query failed: %s", exc)

        yield vms_gauge
        yield vms_os_gauge
        yield nodes_gauge
        yield tenants_gauge
        yield users_gauge
        yield cap_vcpus
        yield cap_vcpus_free
        yield cap_ram
        yield cap_ram_free
        yield cap_disk
        yield cap_disk_free


# ---------------------------------------------------------------------------
# Scrape rendering
# ---------------------------------------------------------------------------

def render_metrics() -> tuple[bytes, str]:
    """Build a fresh registry combining multiproc shards + live DB collector.

    Fresh registry per scrape is the prometheus_client-recommended pattern
    for multiprocess mode; it prevents accidental double-registration and
    lets the state collector's transient queries be scoped to the scrape.
    """
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)  # aggregates shard files
    registry.register(_StateCollector())          # live DB read
    return generate_latest(registry), CONTENT_TYPE_LATEST


# ---------------------------------------------------------------------------
# Flask middleware
# ---------------------------------------------------------------------------

def install(app) -> None:
    """Wire HTTP RED metrics onto a Flask app.

    Skips `/metrics` itself so scrapes don't self-count.
    """
    import time

    from flask import g, request

    @app.before_request
    def _start_timer():
        # perf_counter is monotonic and unaffected by wall-clock adjustments
        g._metrics_start = time.perf_counter()

    @app.after_request
    def _record_request(response):
        start = getattr(g, "_metrics_start", None)
        if start is None:
            return response
        if request.path == "/metrics":
            return response
        endpoint = request.endpoint or "unknown"
        duration = time.perf_counter() - start
        http_requests_total.labels(
            method=request.method, endpoint=endpoint, status=str(response.status_code)
        ).inc()
        http_request_duration_seconds.labels(
            method=request.method, endpoint=endpoint
        ).observe(duration)
        return response


# ---------------------------------------------------------------------------
# Decorators for service-layer instrumentation
# ---------------------------------------------------------------------------

def _outcome(result) -> str:
    """dict-return convention: {'error': ...} means fail, otherwise ok."""
    return "error" if isinstance(result, dict) and "error" in result else "ok"


def track_operation(op_name: str):
    """Wrap a service function that returns a dict; bump vm_operations_total.

    Usage:  @track_operation("edit")
            def vm_edit(...): ...
    """
    from functools import wraps

    def _deco(fn):
        @wraps(fn)
        def _wrapped(*args, **kwargs):
            result = fn(*args, **kwargs)
            vm_operations_total.labels(
                operation=op_name, result=_outcome(result)
            ).inc()
            return result
        return _wrapped
    return _deco


def track_vm_action(fn):
    """Wrap vm_action; derives operation label from the `action` argument.

    Emits `wvc_vm_operations_total{operation="power.<action>"}`.
    """
    from functools import wraps

    @wraps(fn)
    def _wrapped(vm_id, action, *args, **kwargs):
        result = fn(vm_id, action, *args, **kwargs)
        vm_operations_total.labels(
            operation=f"power.{action}", result=_outcome(result)
        ).inc()
        return result
    return _wrapped


def track_vm_create(fn):
    """Wrap vm_create; derives operation label from fields['mode'].

    Emits `wvc_vm_operations_total{operation="create.<mode>"}` and, on success,
    `wvc_vm_created_total{os_family, mode}`. os_family is resolved from the
    catalog image for quick-create (linux/windows); other modes are 'custom'.
    """
    from functools import wraps

    def _os_family_for(fields: dict) -> str:
        mode = (fields or {}).get("mode", "blank")
        if mode == "quick":
            image_id = (fields or {}).get("image_id", "")
            iid = str(image_id).lower()
            if "win" in iid:
                return "windows"
            if any(k in iid for k in ("ubuntu", "debian", "linux", "centos",
                                      "rocky", "alma", "fedora", "rhel")):
                return "linux"
            return "custom"
        return "custom"

    @wraps(fn)
    def _wrapped(fields):
        mode = (fields or {}).get("mode", "blank")
        result = fn(fields)
        outcome = _outcome(result)
        vm_operations_total.labels(
            operation=f"create.{mode}", result=outcome
        ).inc()
        if outcome == "ok":
            vm_created_total.labels(
                os_family=_os_family_for(fields), mode=mode
            ).inc()
        return result
    return _wrapped


def track_vm_migrate(fn):
    """Wrap vm_migrate; records both operation counter and duration histogram.

    On success, `result["live"]` selects the histogram label
    ("live" vs "offline"). Failures still bump the counter but not the
    histogram (a 0.1s "CPU mismatch" refusal isn't a meaningful sample).
    """
    import time as _time
    from functools import wraps

    @wraps(fn)
    def _wrapped(vm_id, target_node_id, allow_shutdown=False):
        start = _time.perf_counter()
        result = fn(vm_id, target_node_id, allow_shutdown=allow_shutdown)
        outcome = _outcome(result)
        vm_operations_total.labels(operation="migrate", result=outcome).inc()
        if outcome == "ok":
            duration = _time.perf_counter() - start
            mode = "live" if isinstance(result, dict) and result.get("live") else "offline"
            vm_migration_duration_seconds.labels(mode=mode).observe(duration)
        return result
    return _wrapped


def track_node_poll(fn):
    """Wrap _poll_node; records node_poll_duration_seconds by success/fail.

    _poll_node returns a metrics dict on success, None on failure.
    """
    import time as _time
    from functools import wraps

    @wraps(fn)
    def _wrapped(node):
        start = _time.perf_counter()
        result = fn(node)
        outcome = "ok" if result is not None else "fail"
        node_poll_duration_seconds.labels(result=outcome).observe(
            _time.perf_counter() - start
        )
        return result
    return _wrapped
