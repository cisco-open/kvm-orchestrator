"""Generator for the kvmaestro Grafana dashboard JSON.

Run:  python3 build_dashboard.py > grafana-dashboard.json

Why a generator instead of hand-written JSON:
- Panels share a lot of boilerplate (datasource, fieldConfig, options).
  Deduping via functions makes the design intent legible and edits cheap.
- Grid positions are computed from a row model, so re-ordering rows or
  changing widths doesn't require hand-updating dozens of {x,y,w,h}.
- Target expressions are the actual PromQL string constants — easy to
  audit against the metric names in controller/app/metrics.py.

Compatibility: schemaVersion=36, pluginVersion=9.0.1 — matches
grafana.example.com (Grafana 8.3.3). If we upgrade Grafana
past v9, bump both here.

Datasource UID `o_-9w1Tvk` is the existing Prometheus datasource on
client-grafana used by the self-service dashboard.
"""
import json
import sys

DS = {"type": "prometheus", "uid": "o_-9w1Tvk"}
PLUGIN = "9.0.1"


# ---------------------------------------------------------------------------
# Target (PromQL query) builder
# ---------------------------------------------------------------------------

def target(expr, legend="", ref="A", *, editor="code", hide=False, instant=False):
    """Build a single Prometheus query target."""
    t = {
        "datasource": DS,
        "editorMode": editor,
        "expr": expr,
        "legendFormat": legend,
        "range": not instant,
        "refId": ref,
    }
    if hide:
        t["hide"] = True
    if instant:
        t["instant"] = True
        t["range"] = False
    return t


# ---------------------------------------------------------------------------
# Panel builders — one per Grafana panel type we use
# ---------------------------------------------------------------------------

def stat(pid, title, targets, *, unit="none", color_mode="value",
         thresholds=None, text_mode="auto", graph_mode="area",
         reduce_calc="lastNotNull", w=4, description=""):
    """A big-number stat panel."""
    if thresholds is None:
        thresholds = {"mode": "absolute", "steps": [{"color": "text", "value": None}]}
    return {
        "id": pid,
        "type": "stat",
        "title": title,
        "description": description,
        "datasource": DS,
        "fieldConfig": {
            "defaults": {
                "color": {"mode": color_mode},
                "mappings": [],
                "thresholds": thresholds,
                "unit": unit,
            },
            "overrides": [],
        },
        "gridPos": {"h": 0, "w": w, "x": 0, "y": 0},  # placed later
        "options": {
            "colorMode": "value",
            "graphMode": graph_mode,
            "justifyMode": "auto",
            "orientation": "auto",
            "reduceOptions": {"calcs": [reduce_calc], "fields": "", "values": False},
            "textMode": text_mode,
        },
        "pluginVersion": PLUGIN,
        "targets": targets,
    }


def gauge(pid, title, targets, *, unit="percent", min_=0, max_=100,
          thresholds=None, w=6, description=""):
    """A radial gauge — good for capacity/utilisation."""
    if thresholds is None:
        thresholds = {"mode": "absolute", "steps": [
            {"color": "green", "value": None},
            {"color": "orange", "value": 70},
            {"color": "red", "value": 90},
        ]}
    return {
        "id": pid,
        "type": "gauge",
        "title": title,
        "description": description,
        "datasource": DS,
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "thresholds"},
                "mappings": [],
                "min": min_,
                "max": max_,
                "thresholds": thresholds,
                "unit": unit,
            },
            "overrides": [],
        },
        "gridPos": {"h": 0, "w": w, "x": 0, "y": 0},
        "options": {
            "orientation": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "showThresholdLabels": False,
            "showThresholdMarkers": True,
        },
        "pluginVersion": PLUGIN,
        "targets": targets,
    }


def timeseries(pid, title, targets, *, unit="short", stacking=None, legend_mode="table",
               w=12, description="", overrides=None, decimals=None):
    """A time-series line panel. `stacking` = 'normal' | 'percent' | None."""
    stacking_conf = {"mode": "none", "group": "A"}
    if stacking == "normal":
        stacking_conf = {"mode": "normal", "group": "A"}
    elif stacking == "percent":
        stacking_conf = {"mode": "percent", "group": "A"}
    defaults = {
        "color": {"mode": "palette-classic"},
        "custom": {
            "axisLabel": "",
            "axisPlacement": "auto",
            "barAlignment": 0,
            "drawStyle": "line",
            "fillOpacity": 10 if stacking else 5,
            "gradientMode": "none",
            "hideFrom": {"legend": False, "tooltip": False, "viz": False},
            "lineInterpolation": "linear",
            "lineWidth": 1,
            "pointSize": 5,
            "scaleDistribution": {"type": "linear"},
            "showPoints": "never",
            "spanNulls": False,
            "stacking": stacking_conf,
            "thresholdsStyle": {"mode": "off"},
        },
        "mappings": [],
        "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]},
        "unit": unit,
    }
    if decimals is not None:
        defaults["decimals"] = decimals
    return {
        "id": pid,
        "type": "timeseries",
        "title": title,
        "description": description,
        "datasource": DS,
        "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
        "gridPos": {"h": 0, "w": w, "x": 0, "y": 0},
        "options": {
            "legend": {"displayMode": legend_mode, "placement": "bottom", "calcs": ["mean", "lastNotNull", "max"]},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
        "pluginVersion": PLUGIN,
        "targets": targets,
    }


def piechart(pid, title, targets, *, unit="short", w=8, description="",
             legend_values=("value", "percent")):
    return {
        "id": pid,
        "type": "piechart",
        "title": title,
        "description": description,
        "datasource": DS,
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "palette-classic"},
                "custom": {"hideFrom": {"legend": False, "tooltip": False, "viz": False}},
                "mappings": [],
                "unit": unit,
            },
            "overrides": [],
        },
        "gridPos": {"h": 0, "w": w, "x": 0, "y": 0},
        "options": {
            "displayLabels": [],
            "legend": {
                "displayMode": "table",
                "placement": "right",
                "showLegend": True,
                "values": list(legend_values),
            },
            "pieType": "donut",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "tooltip": {"mode": "single", "sort": "none"},
        },
        "pluginVersion": PLUGIN,
        "targets": targets,
    }


def bargauge(pid, title, targets, *, unit="short", w=8, description="",
             min_=0, orientation="horizontal", display_mode="lcd"):
    return {
        "id": pid,
        "type": "bargauge",
        "title": title,
        "description": description,
        "datasource": DS,
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "thresholds"},
                "mappings": [],
                "min": min_,
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "green", "value": None},
                    ],
                },
                "unit": unit,
            },
            "overrides": [],
        },
        "gridPos": {"h": 0, "w": w, "x": 0, "y": 0},
        "options": {
            "displayMode": display_mode,
            "orientation": orientation,
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "showUnfilled": True,
        },
        "pluginVersion": PLUGIN,
        "targets": targets,
    }


def barchart(pid, title, targets, *, unit="s", w=12, description="",
             x_field="endpoint", legend_mode="hidden"):
    """Bar chart panel — good for top-N tables. Requires a table-format query
    (usually with `topk(...) by (label)` or an instant query returning several
    labeled series). Grafana transforms series → bars keyed by the given field.
    """
    return {
        "id": pid,
        "type": "barchart",
        "title": title,
        "description": description,
        "datasource": DS,
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "palette-classic"},
                "custom": {
                    "axisLabel": "",
                    "axisPlacement": "auto",
                    "fillOpacity": 80,
                    "gradientMode": "none",
                    "hideFrom": {"legend": False, "tooltip": False, "viz": False},
                    "lineWidth": 1,
                },
                "mappings": [],
                "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]},
                "unit": unit,
            },
            "overrides": [],
        },
        "gridPos": {"h": 0, "w": w, "x": 0, "y": 0},
        "options": {
            "orientation": "horizontal",
            "xTickLabelRotation": 0,
            "xTickLabelSpacing": 0,
            "showValue": "auto",
            "stacking": "none",
            "groupWidth": 0.7,
            "barWidth": 0.9,
            "barRadius": 0,
            "tooltip": {"mode": "single", "sort": "none"},
            "legend": {"displayMode": legend_mode, "placement": "right", "calcs": []},
            "xField": x_field,
        },
        "pluginVersion": PLUGIN,
        "targets": targets,
        "transformations": [
            {
                "id": "labelsToFields",
                "options": {"mode": "columns"},
            },
        ],
    }


# ---------------------------------------------------------------------------
# Panels
# ---------------------------------------------------------------------------
# `wvc_build_info` is a Gauge with a `version` label whose value is always 1;
# we surface the label itself via a table-value mapping trick: we take the
# lastNotNull value (=1) and rely on `textMode="name"` to show the series
# label. Grafana honours legendFormat as the display name for stat panels.

PANEL_10 = stat(
    10, "Controller version",
    [target("wvc_build_info", legend="{{version}}", instant=True)],
    unit="none", text_mode="name", graph_mode="none", w=4,
    description="Version label from wvc_build_info gauge. If this ever shows two versions, a rolling deploy is in-flight or an old worker didn't restart.",
)

PANEL_11 = stat(
    11, "Nodes online",
    [target("sum(wvc_nodes{status=\"online\"})", legend="online", instant=True)],
    unit="none", w=3, graph_mode="none",
    thresholds={"mode": "absolute", "steps": [
        {"color": "red", "value": None},
        {"color": "orange", "value": 3},
        {"color": "green", "value": 4},
    ]},
    color_mode="thresholds",
    description="Count of nodes with status='online'. Amber < full complement, red = fewer than 3.",
)

PANEL_12 = stat(
    12, "VMs running",
    [target("sum(wvc_vms{state=\"running\"})", legend="running", instant=True)],
    unit="none", w=3, graph_mode="area",
    description="Total VMs currently reporting state=running.",
)

PANEL_13 = stat(
    13, "VMs total",
    [target("sum(wvc_vms)", legend="total", instant=True)],
    unit="none", w=3, graph_mode="none",
    description="All VMs known to the controller (any state).",
)

PANEL_14 = gauge(
    14, "Cluster RAM used %",
    [target(
        "100 * (1 - sum(wvc_cluster_ram_mb_available) / sum(wvc_cluster_ram_mb_total))",
        legend="used %", instant=True,
    )],
    unit="percent", w=6,
    description="Sum of available RAM across online nodes vs total. Aggregated at scrape time — the nodes report the truth as libvirt sees it.",
)

PANEL_15 = stat(
    15, "Cluster vCPUs (total)",
    [target("sum(wvc_cluster_vcpus_total)", legend="vCPUs", instant=True)],
    unit="none", w=5, graph_mode="none",
    description="Sum of total physical vCPUs across online nodes. NOTE: `wvc_cluster_vcpus_available` currently equals total (nodes don't decrement free vCPU on VM allocation — libvirt oversubscription model), so a 'used %' panel would be meaningless here. RAM is the real capacity constraint.",
)

PANEL_20 = piechart(
    20, "VMs by state",
    [target("sum by (state) (wvc_vms)", legend="{{state}}", instant=True)],
    w=8, description="Distribution of VMs by libvirt state at scrape time.",
)

PANEL_21 = timeseries(
    21, "VMs by state over time",
    [target("sum by (state) (wvc_vms)", legend="{{state}}")],
    unit="short", stacking="normal", w=16,
    description="Stacked view of VMs-by-state; watch for spikes in `crashed` or sudden movement between `running` and `stopped`.",
)

PANEL_30 = bargauge(
    30, "Nodes by status",
    [target("sum by (status) (wvc_nodes)", legend="{{status}}", instant=True)],
    unit="none", w=8, orientation="horizontal", display_mode="lcd",
    description="Node status breakdown — normally all online. `offline` appearing means node_monitor's SSH poll is failing.",
)

PANEL_31 = timeseries(
    31, "Cluster RAM (MB)",
    [
        target("sum(wvc_cluster_ram_mb_total)", legend="total", ref="A"),
        target("sum(wvc_cluster_ram_mb_available)", legend="available", ref="B"),
    ],
    unit="deckbytes",  # deckbytes = base-1024 MB — Grafana renders as "MiB", "GiB" auto
    w=16,
    description="Cluster RAM total (physical) vs available (free per libvirt). Gap = allocated to running VMs. NOTE: the base-1024 unit converts MB→GiB display auto, which is close enough for a capacity dashboard.",
)

PANEL_40 = timeseries(
    40, "HTTP request rate by status class",
    [
        target("sum by (status) (rate(wvc_http_requests_total{status=~\"2..\"}[5m]))", legend="2xx", ref="A"),
        target("sum by (status) (rate(wvc_http_requests_total{status=~\"3..\"}[5m]))", legend="3xx", ref="B"),
        target("sum by (status) (rate(wvc_http_requests_total{status=~\"4..\"}[5m]))", legend="4xx", ref="C"),
        target("sum by (status) (rate(wvc_http_requests_total{status=~\"5..\"}[5m]))", legend="5xx", ref="D"),
    ],
    unit="reqps", stacking="normal", w=12,
    description="Rolling 5-minute request rate, stacked by HTTP status class. A healthy controller is ~all 2xx with a low background of 4xx (auth challenges, 404s).",
    overrides=[
        {"matcher": {"id": "byName", "options": "4xx"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "orange"}}]},
        {"matcher": {"id": "byName", "options": "5xx"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]},
        {"matcher": {"id": "byName", "options": "2xx"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "green"}}]},
        {"matcher": {"id": "byName", "options": "3xx"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "blue"}}]},
    ],
)

PANEL_41 = timeseries(
    41, "HTTP request latency percentiles",
    [
        target("histogram_quantile(0.50, sum by (le) (rate(wvc_http_request_duration_seconds_bucket[5m])))", legend="p50", ref="A"),
        target("histogram_quantile(0.95, sum by (le) (rate(wvc_http_request_duration_seconds_bucket[5m])))", legend="p95", ref="B"),
        target("histogram_quantile(0.99, sum by (le) (rate(wvc_http_request_duration_seconds_bucket[5m])))", legend="p99", ref="C"),
    ],
    unit="s", w=12,
    description="Global HTTP latency percentiles across all endpoints. p99 sustained > 1s is worth investigating; migrate/create endpoints will legitimately push p99 up.",
)

PANEL_50 = barchart(
    50, "Top 10 slowest endpoints (p95, last 5m)",
    [target(
        "topk(10, histogram_quantile(0.95, sum by (endpoint, le) (rate(wvc_http_request_duration_seconds_bucket[5m]))))",
        legend="{{endpoint}}", instant=True,
    )],
    unit="s", w=12, x_field="endpoint",
    description="Ten slowest Flask endpoints by 5-minute p95 latency. Endpoints like `vms.migrate_vm` legitimately dominate; a sudden `vms.list_vms` climb usually indicates DB lag.",
)

PANEL_51 = timeseries(
    51, "HTTP 5xx error rate",
    [target(
        "sum by (endpoint) (rate(wvc_http_requests_total{status=~\"5..\"}[5m]))",
        legend="{{endpoint}}",
    )],
    unit="reqps", stacking="normal", w=12,
    description="Per-endpoint 5xx rate. Should be flat zero. Any sustained non-zero line here is an alert-worthy regression.",
)

PANEL_60 = timeseries(
    60, "VM operation rate by operation",
    [target(
        "sum by (operation) (rate(wvc_vm_operations_total[5m]))",
        legend="{{operation}}",
    )],
    unit="reqps", stacking="normal", w=12,
    description="Rate of admin-initiated VM operations (create.*, power.*, edit, delete, migrate) grouped by op. Not stacked by result — that's the next panel.",
)

PANEL_61 = timeseries(
    61, "VM migration duration percentiles",
    [
        target("histogram_quantile(0.50, sum by (mode, le) (rate(wvc_vm_migration_duration_seconds_bucket[30m])))", legend="{{mode}} p50", ref="A"),
        target("histogram_quantile(0.95, sum by (mode, le) (rate(wvc_vm_migration_duration_seconds_bucket[30m])))", legend="{{mode}} p95", ref="B"),
    ],
    unit="s", w=12,
    description="Migration duration percentiles by mode (live vs offline). Only successful migrations are observed — a failure that returned in 0.1s (CPU mismatch refusal) is not in this histogram.",
)

PANEL_70 = timeseries(
    70, "vm_sync iteration duration",
    [
        target("histogram_quantile(0.50, sum by (le) (rate(wvc_vm_sync_duration_seconds_bucket[10m])))", legend="p50", ref="A"),
        target("histogram_quantile(0.95, sum by (le) (rate(wvc_vm_sync_duration_seconds_bucket[10m])))", legend="p95", ref="B"),
        target("histogram_quantile(0.99, sum by (le) (rate(wvc_vm_sync_duration_seconds_bucket[10m])))", legend="p99", ref="C"),
        target("sum(rate(wvc_vm_sync_iterations_total{result=\"error\"}[10m]))", legend="errors/s", ref="D"),
    ],
    unit="s", w=12,
    description="How long each vm_sync loop iteration takes across all online nodes. p95 > 5s means one of the nodes is slow to SSH. The 'errors/s' series has different units but overlaying it here saves a panel; watch for any non-zero.",
    overrides=[
        {"matcher": {"id": "byName", "options": "errors/s"}, "properties": [
            {"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}},
            {"id": "custom.axisPlacement", "value": "right"},
        ]},
    ],
)

PANEL_71 = timeseries(
    71, "node_poll duration by result",
    [
        target("histogram_quantile(0.95, sum by (result, le) (rate(wvc_node_poll_duration_seconds_bucket[10m])))", legend="p95 {{result}}", ref="A"),
        target("sum by (result) (rate(wvc_node_poll_duration_seconds_count{result=\"fail\"}[10m]))", legend="fail rate", ref="B"),
    ],
    unit="s", w=12,
    description="Per-node SSH poll duration p95 (ok vs fail buckets separated) and a fail-rate overlay. Sustained non-zero fail rate means SSH into a node is broken; check node_monitor logs.",
    overrides=[
        {"matcher": {"id": "byName", "options": "fail rate"}, "properties": [
            {"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}},
            {"id": "custom.axisPlacement", "value": "right"},
        ]},
    ],
)

PANEL_80 = timeseries(
    80, "LDAP bind rate",
    [target(
        "sum by (kind, result) (rate(wvc_ldap_binds_total[5m]))",
        legend="{{kind}} / {{result}}",
    )],
    unit="reqps", stacking="normal", w=12,
    description="LDAP bind rate broken down by kind (service|user|group_query) and result (ok|fail). A sudden spike of user/fail = someone brute-forcing or an AD outage.",
    overrides=[
        {"matcher": {"id": "byRegexp", "options": ".*/ fail"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]},
    ],
)

PANEL_81 = timeseries(
    81, "LDAP bind failure ratio (5m)",
    [target(
        "sum by (kind) (rate(wvc_ldap_binds_total{result=\"fail\"}[5m])) "
        "/ ignoring(result) group_left "
        "clamp_min(sum by (kind) (rate(wvc_ldap_binds_total[5m])), 0.001)",
        legend="{{kind}}",
    )],
    unit="percentunit", w=12,
    description="Fraction of LDAP binds that failed, per kind. `service` failing = the controller can't talk to AD at all; `user` failing spike = brute-force or wrong password rollout; `group_query` failing = AD ACL / OU permissions changed. The clamp_min prevents 0/0 spikes when no binds are happening.",
)


PANELS = [
    PANEL_10, PANEL_11, PANEL_12, PANEL_13, PANEL_14, PANEL_15,
    PANEL_20, PANEL_21,
    PANEL_30, PANEL_31,
    PANEL_40, PANEL_41,
    PANEL_50, PANEL_51,
    PANEL_60, PANEL_61,
    PANEL_70, PANEL_71,
    PANEL_80, PANEL_81,
]


# ---------------------------------------------------------------------------
# Row layout — assign x/y/w/h to each panel based on desired grid
# ---------------------------------------------------------------------------

# Row spec: (row_y, row_h, [(panel_id, width), ...])   widths must sum to 24
LAYOUT = [
    (0,   3, [(10, 4), (11, 3), (12, 3), (13, 3), (14, 6), (15, 5)]),
    (3,   8, [(20, 8), (21, 16)]),
    (11,  8, [(30, 8), (31, 16)]),
    (19,  8, [(40, 12), (41, 12)]),
    (27,  8, [(50, 12), (51, 12)]),
    (35,  8, [(60, 12), (61, 12)]),
    (43,  8, [(70, 12), (71, 12)]),
    (51,  8, [(80, 12), (81, 12)]),
]


def apply_layout(panels, layout):
    by_id = {p["id"]: p for p in panels}
    for y, h, row in layout:
        row_w_sum = sum(w for _, w in row)
        if row_w_sum != 24:
            raise ValueError(f"row y={y} widths sum to {row_w_sum}, must be 24")
        x = 0
        for pid, w in row:
            if pid not in by_id:
                raise KeyError(f"panel id {pid} referenced in layout but not defined")
            by_id[pid]["gridPos"] = {"h": h, "w": w, "x": x, "y": y}
            x += w
    # Sanity check: every panel got a gridPos with a real h/w
    for p in panels:
        gp = p["gridPos"]
        if gp["h"] == 0 or gp["w"] == 0:
            raise ValueError(f"panel {p['id']} '{p['title']}' has no layout entry")


# ---------------------------------------------------------------------------
# Dashboard shell
# ---------------------------------------------------------------------------

def build_dashboard():
    apply_layout(PANELS, LAYOUT)
    # Order panels by (y, x) for a tidy JSON that mirrors visual order.
    panels_sorted = sorted(PANELS, key=lambda p: (p["gridPos"]["y"], p["gridPos"]["x"]))
    return {
        "annotations": {
            "list": [
                {
                    "builtIn": 1,
                    "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                    "enable": True,
                    "hide": True,
                    "iconColor": "rgba(0, 211, 255, 1)",
                    "name": "Annotations & Alerts",
                    "target": {"limit": 100, "matchAny": False, "tags": [], "type": "dashboard"},
                    "type": "dashboard",
                },
            ],
        },
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 0,
        "id": None,
        "links": [],
        "liveNow": False,
        "panels": panels_sorted,
        "refresh": "30s",
        "schemaVersion": 36,
        "style": "dark",
        "tags": ["kvmaestro", "kvm"],
        "templating": {"list": []},
        "time": {"from": "now-6h", "to": "now"},
        "timepicker": {},
        "timezone": "",
        "title": "kvmaestro",
        "uid": "wvc-main",
        "version": 1,
        "weekStart": "",
    }


if __name__ == "__main__":
    json.dump(build_dashboard(), sys.stdout, indent=2)
    sys.stdout.write("\n")
