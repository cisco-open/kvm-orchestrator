# kvmaestro metrics — Grafana dashboard

This directory ships the observability side-artefacts for the metrics endpoint
introduced in **v0.16.0** (see `controller/app/metrics.py`).

## Files

| File | What it is |
|------|-----------|
| `grafana-dashboard.json` | 20-panel dashboard, ready to import. Regenerated from `build_dashboard.py`. |
| `build_dashboard.py` | Python generator that emits the JSON above. Edit this, then re-run. |
| `prometheus-scrape.yml` | Snippet you paste into the Prometheus `scrape_configs` list. |

## Prerequisites

1. A running v0.16.0+ controller with `/metrics` reachable — e.g. `http://dev.example.com:8000/metrics`.
2. Prometheus scraping that endpoint (see below).
3. Grafana with the same Prometheus configured as a datasource (uid `o_-9w1Tvk` on `grafana.example.com`).

## Prometheus scrape config

Append the contents of `prometheus-scrape.yml` to the `scrape_configs:` list in
`prometheus.yml` and reload (`kill -HUP $(pidof prometheus)` or `systemctl reload prometheus`).

Sanity-check by hitting `http://<prometheus>:9090/api/v1/targets` and looking
for the `kvmaestro` job in `state=up`.

## Importing the dashboard

### Option A — UI (fastest)

1. Open `https://grafana.example.com`, log in as admin
2. Left sidebar → **Dashboards** → **New** → **Import**
3. Click **Upload JSON file**, pick `grafana-dashboard.json`
4. On the import screen leave the Prometheus datasource as `Prometheus-scrape` (uid `o_-9w1Tvk` — already baked into the file)
5. Click **Import**. Dashboard lands at uid `wvc-main`, path `/d/wvc-main/kvmaestro`.

### Option B — API (scriptable)

```sh
# Get an admin API token from Grafana: Configuration → API keys → New key (Admin role)
TOKEN='<paste-here>'
curl -sk -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -X POST \
     -d @<(jq '{dashboard: ., overwrite: true, folderId: 0}' grafana-dashboard.json) \
     https://grafana.example.com/api/dashboards/db
```

The wrapping via `jq` is because Grafana's `POST /api/dashboards/db` expects
`{dashboard: {...}, overwrite: true, folderId: N}` — not the raw dashboard.

## Editing the dashboard

Prefer editing `build_dashboard.py` and re-running it (`python3 build_dashboard.py > grafana-dashboard.json`)
rather than editing the JSON by hand — the JSON is 1900+ lines of boilerplate
and diffing it after Grafana's "save changes" mangling is painful. The generator
is authoritative.

If you must edit in Grafana's UI: save the dashboard from Grafana with a
different uid (e.g. `wvc-scratch`) while experimenting, then port the changes
back into `build_dashboard.py`.

## Panel index

| Row | Panels |
|-----|--------|
| Header | Controller version · Nodes online · VMs running · VMs total · Cluster RAM used % · Cluster vCPUs total |
| VM state | VMs by state (donut) · VMs by state over time (stacked) |
| Node capacity | Nodes by status · Cluster RAM total vs available |
| HTTP | Request rate by status class · Latency percentiles (p50/p95/p99) |
| HTTP detail | Top 10 slowest endpoints (p95) · 5xx error rate |
| VM operations | Operation rate by operation · Migration duration percentiles |
| Background loops | vm_sync iteration duration · node_poll duration by result |
| LDAP | Bind rate · Bind failure ratio |

## Suggested alert rules (not shipped yet)

These aren't ingested by Prometheus — copy into an `alerts.yml` if wanted:

```yaml
groups:
  - name: kvmaestro
    rules:
      - alert: WvcControllerDown
        expr: up{job="kvmaestro"} == 0
        for: 2m
        annotations:
          summary: "kvmaestro /metrics has been unreachable for 2 minutes"

      - alert: WvcNodesFewerThanExpected
        expr: sum(wvc_nodes{status="online"}) < 4
        for: 5m
        annotations:
          summary: "Fewer than 4 KVM nodes online"

      - alert: Wvc5xxSpike
        expr: sum(rate(wvc_http_requests_total{status=~"5.."}[5m])) > 0.1
        for: 5m
        annotations:
          summary: "Sustained 5xx errors from the controller"

      - alert: WvcSyncErrors
        expr: rate(wvc_vm_sync_iterations_total{result="error"}[10m]) > 0
        for: 10m
        annotations:
          summary: "vm_sync loop is erroring"

      - alert: WvcNodePollFailing
        expr: rate(wvc_node_poll_duration_seconds_count{result="fail"}[10m]) > 0
        for: 10m
        annotations:
          summary: "A KVM node is failing SSH poll — check node_monitor logs"

      - alert: WvcLDAPServiceFailing
        expr: |
          sum(rate(wvc_ldap_binds_total{kind="service",result="fail"}[5m]))
            /
          clamp_min(sum(rate(wvc_ldap_binds_total{kind="service"}[5m])), 0.001)
            > 0.5
        for: 5m
        annotations:
          summary: "Half of service-account LDAP binds are failing"
```
