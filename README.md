# KVM Orchestrator

**Self-service VM management for your own KVM/libvirt hosts.**

KVM Orchestrator is an open-source control plane for managing virtual machines across a
fleet of KVM/libvirt hypervisors. It gives you a clean web UI and an API for the
full VM lifecycle — create, start/stop, hard power-off, reboot, suspend, edit,
live & offline migrate, in-browser VNC console — plus multi-tenancy, LDAP/Active
Directory auth, an in-browser VNC console, and an optional time-to-live
lifecycle engine, all over hypervisors you already run on commodity hardware.

It is **not** a hyperconverged appliance and it does **not** replace your
hypervisor. It sits *on top of* your existing KVM hosts (which stay generic
libvirt machines) and adds the management, self-service, and automation layer.

---

## Why KVM Orchestrator?

If you run KVM/libvirt and want a management layer, your options are typically a
full platform (Proxmox, oVirt/OLVM, OpenStack) or a commercial product
(Nutanix, VMware). KVM Orchestrator targets a specific gap those don't fill well:

- **Self-service, multi-tenant VM provisioning.** Hand VMs to end users with
  per-tenant visibility, quotas, and an API — not just a cluster-admin console.
  Tenancy maps to your LDAP/AD groups.
- **API-first and embeddable.** Every action is a REST call. The React UI is one
  client; automate the rest. Integrate with your own IdM, DNS, ticketing, or
  chatops instead of adapting to a closed platform.
- **Decoupled from the hypervisor.** Your KVM hosts stay plain libvirt machines
  with any host OS/config. The control plane runs separately and scales
  independently — no per-node controller VM, no proprietary storage format, no
  hardware HCL to satisfy.
- **Runs on what you have.** Commodity servers, your existing Ceph/RBD or local
  storage, standard KVM. No licensing cost.
- **Programmable VM lifecycle / TTL.** An optional engine gives every VM a term
  (e.g. one year), warns owners as expiry nears, lets them renew in one click,
  and reclaims abandoned VMs — a built-in answer to VM sprawl that most
  platforms lack.
- **In-browser VNC console** with a stateless, horizontally-scalable proxy, and
  **live + offline migration** (offline migration works across hosts with
  differing CPU features).
- **Runtime branding.** Ship the default debranded "KVM Orchestrator" identity, or set
  one env var to apply your own name/logo — no rebuild.

**When KVM Orchestrator is the right fit:** cost-sensitive KVM shops, internal/dev
clouds, CI fleets, labs, and service providers that need to *offer* VMs to users
with automation and tenancy.

**When it isn't (yet):** if you need turnkey, vendor-supported infrastructure
with built-in backup/snapshot/DR orchestration and a support SLA, a mature
platform (Proxmox) or commercial HCI (Nutanix) is the safer choice. See
[Status & limitations](#status--limitations).

---

## Status & limitations

> ### ⚠️ Tested on Kubernetes only
>
> Every component ships as a container, **but KVM Orchestrator has only ever been
> deployed and tested on Kubernetes** (via the bundled Helm chart). The
> Kubernetes/Helm path is the **only supported, validated way to run it.**
>
> A plain-Docker / docker-compose workflow is described further down for local
> development convenience, but it is **not tested, not supported, and not
> guaranteed to work** — treat it as a starting point you may have to fix, not a
> production option.

Other things to know before you adopt it:

- **Young project.** It grew inside a single lab and is newly open-sourced.
  Expect rough edges; there are no external production references yet.
- **You operate it.** No vendor support. You run and patch the control plane,
  the hypervisors, and the storage yourself. A competent Linux/KVM/Kubernetes
  team is assumed.
- **Feature gaps vs. mature platforms:** no built-in snapshot/backup/restore or
  replication/DR orchestration, no per-VM metrics yet (host-node metrics only),
  no container (LXC) workloads. The HA engine is human-in-the-loop by design.
- **Config to supply:** you must provide your own container registry, storage
  (Ceph RBD or shared storage for migration without disk copy), an ingress, and
  TLS. See [`helm/kvm-orchestrator/values/values.example.yaml`](helm/kvm-orchestrator/values/values.example.yaml).

---

## Quick start (Kubernetes — the supported path)

1. Build and push the three images (`api`, `ui`, `console-proxy`) to your
   registry — see [Build & release](#build--release).
2. Copy the example values and fill in your registry, ingress host, storage,
   secrets, and (optionally) LDAP:
   ```sh
   cp helm/kvm-orchestrator/values/values.example.yaml my-values.yaml
   # edit my-values.yaml — replace every CHANGE_ME / *.example.com placeholder
   ```
3. Install:
   ```sh
   helm upgrade --install kvm-orchestrator helm/kvm-orchestrator \
     -n kvm-orchestrator --create-namespace -f my-values.yaml
   ```
4. Log in as the bootstrap admin (`admin` / your `ADMIN_PASSWORD`), add your KVM
   hosts, then configure LDAP from the **Users** tab if you want directory auth.

See [Deployment](#deployment) for the full walkthrough.

---

A KVM VM management platform. The current release is tracked in
[`controller/app/version.py`](controller/app/version.py); see
[Version History](#version-history).

---

## Features

- **Dashboard** — live VM and node counts
- **Nodes** — add/remove KVM hypervisors, SSH key management
- **Node detail** — per-node resource allocation bars (vCPU/RAM), networks and storage in use, VM list
- **Virtual Machines** — full VM lifecycle: start, stop, reboot, suspend, resume, console, migrate, edit
- **Networks** — grouped view of all VLANs/bridges in use across the fleet; click to expand hosts and VMs
- **Storage** — grouped view of all storage pools/datastores; click to expand hosts and VMs
- **VM Console** — in-browser VNC via noVNC; token-authenticated WebSocket through console proxy
- **VM Migration** — live migration (same CPU arch) and offline migration (CPU mismatch or stopped VM)
- **Edit VM** — resize vCPUs, RAM, disk; admin: rename, change network, set boot order
- **VM Lifecycle / 1-year TTL** — every VM gets a 12-month term; owners (and tenant AD-group members) are warned on Webex as expiry nears and can **Renew** in one click (UI button or Webex deep-link). Unrenewed VMs are shut down at expiry and cannot be restarted until renewed; after a further grace period Ops is asked (never automatic) to delete. Admin **exempt** toggle for long-lived infra, and an admin **runtime on/off** switch (no redeploy).

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Flask (Python 3.11), raw pymysql, Celery + Redis |
| Frontend | React 18 + TypeScript + Vite + TailwindCSS v4 |
| Database | MySQL 8 |
| Auth | Bootstrap admin (env password) + LDAP (ldap3, DB-configured), JWT tokens |
| Console | noVNC (browser VNC), asyncssh direct-tcpip tunnel |
| Storage | Ceph RBD (shared, enables HA migration without disk copy) |
| Deployment | Docker containers (dev), Kubernetes/Helm (prod) |

## Authentication & admin accounts

Two ways in:

1. **Bootstrap admin** — a built-in local account, username `admin`, whose
   password is the `ADMIN_PASSWORD` env var (empty = disabled). It **bypasses
   LDAP entirely** and is always an admin. Use it to administer a fresh
   deployment before any directory is configured, and as a break-glass login if
   LDAP is later misconfigured or unreachable.

2. **LDAP / Active Directory** — the normal path for everyone else. A user binds,
   the service account resolves their groups, and membership of the configured
   **admin group DN** grants admin rights (persisted to `users.is_admin`).

**LDAP is configured from the UI, not env vars.** Log in as the bootstrap admin
and open the **Users** tab (admin-only) to set the server URI, base-user config
(base DN, user filter, bind DN/password) and admin config (group base DN, admin
group DN). Settings live in the `app_settings` table and take effect
immediately — **no redeploy**. The bind password is stored **encrypted at rest**
(Fernet, key derived from `FLASK_SECRET_KEY`) and is **write-only** in the UI
(never returned; the form shows only whether one is set).

The historical `LDAP_*` env vars are now a one-time **seed**: on first migration
(`0016`) they are copied into `app_settings`, after which the DB rows are
authoritative and the env vars are ignored at runtime. A fresh open-source
install can leave them blank and configure everything in the UI.

> Rotating `FLASK_SECRET_KEY` invalidates the stored (encrypted) bind password —
> re-enter it in the Users tab afterwards.

## Architecture

```
Browser → nginx (port 80) → /api/* → Flask/gunicorn (port 8000)
                          → /       → React SPA (built static files)

Browser ←→ WebSocket (port 8765) ← console-proxy ←→ asyncssh ←→ KVM node VNC

Flask ←→ MySQL 8 (port 3306)
Flask ←→ Redis 7 (port 6379)  ← Celery workers + beat
Flask ←→ KVM node agents (HTTPS, pre-shared key)
```

## Redis Usage

Redis is the short-lived, in-memory side-channel for things SQL is the wrong tool for. **Treat it as ephemeral — nothing in Redis needs to survive a container restart, and nothing needs to be migrated when moving from dev to test to prod.** Stand up a fresh `redis:7` container in each environment.

Three concrete uses today:

| Category | Keys | Lifetime | Recovery on fresh Redis |
|---|---|---|---|
| **Console tokens** | `console_token:<uuid>` | 60-second TTL, single-use (atomic GET-and-DELETE) | Tokens in flight at cutover die; user clicks "Console" again |
| **Node CPU/RAM sparklines** | `node_metrics:<node_id>` (ZSET) | Trimmed to `NODE_METRIC_WINDOW` (default 3600 s); refreshed every `HEARTBEAT_INTERVAL` (default 10 s) | Sparklines empty for one poll cycle, fully refilled within an hour |
| **Poll-cycle locks** | `node_metrics_lock:<node_id>` | TTL ≤ poll interval | Self-healing; copying these across envs would be *bad* (stale lock could suppress one cycle) |

Also scaffolded but not in active use: Celery broker/result backend (`config.py:23-25`), JWT blocklist (`TODO` in `auth.py:112`).

The default Redis container is configured with periodic RDB snapshotting (`save 3600 1 300 100 60 10000`, `appendonly no`) which is fine for surviving a single-container restart, but **the on-disk `dump.rdb` should never be treated as a backup target**. All durable state lives in MySQL.

## VM Console — How It Works

Four-hop architecture: **browser → nginx → console-proxy → SSH tunnel → libvirt VNC**. The proxy is deliberately dumb — it never talks to MySQL, never authenticates a user, doesn't know what a VM is. Everything it needs to do its job arrives in a single Redis blob with a 60-second TTL.

### End-to-end flow

```
[Browser]          [nginx]       [controller]      [Redis]      [console-proxy]      [KVM host]
   │                  │               │               │                │                  │
1. │── click ────────>│── POST /api/vms/<id>/console ─────────────────>│                  │
   │                  │               │── SSH "virsh vncdisplay" ─────────────────────────>│ ── :0
   │                  │               │── SETEX console_token:<uuid> 60 ─>│               │
   │<─ {token, /console-ws}           │               │                │                  │
   │                  │               │               │                │                  │
2. │── open /console.html?token=… ─> served by nginx as separate SPA   │                  │
   │   (loads noVNC dynamically — kept out of the main bundle)         │                  │
   │                  │               │               │                │                  │
3. │── WS Upgrade /console-ws?token=… ────────────────────────────────>│                  │
   │                  │               │               │── GETDEL ─────>│                  │
   │                  │               │               │                │── asyncssh.connect ──>
   │                  │               │               │                │── open_connection(127.0.0.1, vnc_port) ──> VNC
   │                  │               │               │                │                  │
4. │<================ bidirectional bytes: noVNC <-> WS <-> nginx <-> proxy <-> SSH tunnel <-> VNC server =================>│
   │                  │               │               │                │                  │
5. │── close tab ─────│── close WS ───────────────────────────────────>│── conn.close()  │
```

### Step-by-step

**1. Token issue (controller)** — `vm_service.vm_console()` at `controller/app/services/vm_service.py:397-448`:
- `_assert_can_see_vm(vm_id)` (`api/vms.py:376`) enforces tenant visibility. **This is the authorisation moment** — once past it, the rest of the path trusts the token
- Joins `vms`, `nodes`, and `ssh_keys` to get the node's IP and SSH private key (`_get_vm_with_node`, `vm_service.py:64-90`)
- SSHes to the node and runs `virsh vncdisplay '<vm name>'` to discover the VNC display number; `vnc_port = 5900 + display`
- Stores a UUID-keyed blob in Redis with 60-second TTL containing `{vm_id, user_id, node_ip, ssh_user, ssh_key, vnc_port}`
- Returns `{"token": "<uuid>", "wsUrl": "/console-ws"}` to the browser

**2. Console page loads (browser)** — `frontend/src/pages/Console.tsx`:
- Opens `/console.html?token=…&vm=<name>` — a **second HTML entry point** built by Vite alongside `index.html` (see `console-*.js` in the dist listing). Completely separate React tree (`console-main.tsx`) so the console window can be popped out without sidebar/router/auth-context bloat
- Constructs `wss://host/console-ws?token=…` (or `ws://` on HTTP)
- Dynamic-imports `@novnc/novnc` — keeps the 182 KB `rfb-*.js` out of the main bundle

**3. WebSocket upgrade (nginx)** — `kvm-orchestrator-ui` nginx config:
```nginx
location /console-ws {
    proxy_pass http://kvm-orchestrator-console-proxy:8765;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
}
```
Same-origin trick avoids CORS pain; TLS terminates once at nginx so the proxy itself runs plain `ws://`. The hour-long timeout lets idle console sessions stay open.

**4. Token consumption + SSH tunnel (console-proxy)** — `console-proxy/proxy/main.py:22-56` and `vnc_bridge.py`:
- `validate_token()` does an **atomic GET-and-DELETE Redis pipeline** (`token_auth.py:33-37`) — token consumed on first use, cannot be replayed
- `handle_vnc_session()` parses the PEM key from the token, opens an `asyncssh.connect()` to `node_ip`
- Opens a **direct-tcpip channel** to `127.0.0.1:<vnc_port>` on the remote — the SSH equivalent of `ssh -L`. **Why this matters**: libvirt's VNC servers bind to `127.0.0.1` on the KVM host. They're not reachable from the network. The SSH tunnel is the only way in
- Two coroutines bridge bytes both ways with `asyncio.gather()`. The proxy is byte-blind — no RFB parsing, noVNC and the QEMU VNC server speak RFB directly through the pipe

**5. Teardown** — browser closes the tab → WebSocket close propagates → `gather()` returns → `finally` block calls `conn.close()` on SSH → tunnel gone. No persistent state.

### Security model

| Hop | Trust mechanism |
|---|---|
| Browser → controller | Standard JWT auth. The controller is the **only** component that authenticates users |
| Controller → token | Visibility-gated. `_assert_can_see_vm()` enforces tenant rules at issue time |
| Token → proxy | The token is the capability: 60-second TTL + single-use + SSH key inside the blob |
| Proxy → libvirt | SSH, with host key verification disabled (`known_hosts=None`) — internal lab nodes |

**The neat consequence**: scaling the proxy is trivial. Add a second container, put both behind nginx with `least_conn`, and any token works at any proxy because the validation source-of-truth is Redis. No sticky-session requirement.

**The trade-off**: the SSH private key sits in the Redis token blob for up to 60 seconds. Alternative designs (long-lived proxy↔controller connection, proxy with its own DB creds) add ops surface area. The 60-second window plus the atomic single-use delete is the price paid.

### Troubleshooting

| Symptom | Likely cause | Where to look |
|---|---|---|
| Browser: "VM has no VNC display" | VM is off or no `<graphics type='vnc'/>` in domain XML | `virsh vncdisplay <name>` on the node directly |
| Browser: "Invalid or expired token" | > 60 s between click and WS open, or token already consumed | Controller logs: token issued? Proxy logs: token validation failed? |
| Browser: "Connection lost — VM may have shut down" | VNC server gone (VM shut down/crashed) or SSH connection died mid-stream | Proxy logs `VNC session ended: <ip>:<port>` |
| Browser: connecting forever | nginx not proxying upgrades, or proxy container not running | `docker logs kvm-orchestrator-console-proxy`, check nginx upgrade headers |
| "No SSH key for this node" 400 from controller | `nodes.ssh_key_id` is NULL or joined `ssh_keys` row is missing | Admin → Nodes → SSH Keys |

## VM Lifecycle — 1-Year TTL

Every VM carries a 12-month term (`vms.expires_at`, set at create and reset on
every renew). A daily Celery-beat task (`app.tasks.vm_expiry.check_vm_expiry` →
`services/lifecycle_engine.py`) drives a grace machine that warns, shuts down,
and finally asks Ops to delete — **it never deletes a VM automatically**.

### Timeline (real values; all offsets env-tunable)

| When | Action |
|---|---|
| Any time | Owner clicks **Renew** (UI button or Webex deep-link) → term extended 12 months; a shut-down VM is powered back on |
| **11 months** | Warn owner + (for real tenants) every tenant AD-group member on Webex, with a `.../vms?renew=<id>` deep-link |
| **11.5 months** | Repeat the warning |
| **12 months** (expiry) | **Shut down** the VM + warn. It **cannot be restarted** (`start`/`reboot`/`resume` are blocked) until renewed |
| **12.5 months** | Repeat the shut-down notice |
| **13 months** | Post a **delete request** to the Ops room with an admin deep-link. The VM is **not** deleted — an admin actions it |

`lifecycle_state` transitions `active → expiring → auto_stopped → pending_delete`.
Renewing from any state returns it to `active`.

### Recipients

Warnings go to the VM **owner** and, when the VM belongs to a **real** tenant
(admin-created, bound to an AD group — not the built-in SHARED/PERSONAL/DEFAULT),
to **every member of that tenant's AD group**, resolved **live from your AD** via
`tenants.ad_group_dn` (not the login-populated `user_tenants` cache, which can be
empty). Emails come from `users.email` or fall back to `{sAMAccountName}@<EMAIL_DOMAIN>`.
Owner-less infra VMs in a real tenant notify the tenant; truly ownerless VMs fall
back to the Ops room.

### Controls

- **Renew** — owner or admin. `POST /api/vms/<id>/renew`. Also restarts an
  auto-stopped VM.
- **Exempt** (admin) — `POST /api/vms/<id>/exempt` `{exempt}`. Exempt VMs are
  never warned/stopped/flagged. Migration 0014 auto-exempts discovered infra
  VMs (`owner_user_id IS NULL`) so nothing is actioned on day one.
- **Runtime on/off** (admin) — header button on the VMs page, or
  `POST /api/vms/lifecycle/settings` `{enabled}`. Persists to the `ha_settings`
  table (`lifecycle_enabled`) and takes effect **without a redeploy** — the
  engine reads it at the top of every run. `GET /api/vms/lifecycle/settings`
  returns `{enabled, source, env_default}` (`source: runtime|env_default`).

### Configuration (env)

| Var | Default | Meaning |
|---|---|---|
| `LIFECYCLE_ENABLED` | `false` | Startup default for the runtime switch (env is only the seed; the `ha_settings` row wins once set) |
| `LIFECYCLE_DESTRUCTIVE` | `true` | When **false**, the engine NEVER runs a real `virsh stop`/`undefine` — it only simulates + notifies. Set **`false`** on any non-production control plane that points at the SAME physical hosts as production, so it can never stop/delete a real VM |
| `LIFECYCLE_TTL_DAYS` | `365` | Term length |
| `LIFECYCLE_WARN1_DAYS_BEFORE` | `30` | First warning (≈11 mo) |
| `LIFECYCLE_WARN2_DAYS_BEFORE` | `15` | Second warning (≈11.5 mo) |
| `LIFECYCLE_STOP_GRACE_DAYS` | `0` | Days after expiry to auto-stop (12 mo) |
| `LIFECYCLE_POSTSTOP_WARN_DAYS` | `15` | Repeat stop warning (12.5 mo) |
| `LIFECYCLE_OPS_DELETE_DAYS` | `30` | Days after expiry to raise the Ops delete request (13 mo) |
| `WEBEX_BOT_TOKEN` / `WEBEX_HA_ROOM_ID` | — | Bot token + Ops room; reused from the HA notification config. `WEBEX_DEEP_LINK_BASE` builds renew links |

Notifications reuse the HA Webex bot/room; `ENVIRONMENT=test` prefixes messages
with `[***TEST***]`. To avoid double-notifying real users, **only one environment
should have the checker enabled at a time** — keep the non-production stack's
`LIFECYCLE_ENABLED=false` (and/or leave its runtime switch off) whenever prod is
armed.

### Schema (migrations 0014–0015)

`vms` gains `expires_at`, `renewed_at`, `exempt`, `lifecycle_state`. The
append-only `vm_lifecycle_events` ledger (0013) records `create`/`delete`/
`renew`/`warn:*`/`auto_stop`/`ops_delete_req`/`exempt`/`unexempt`; 0015 widens
its `event` column to `VARCHAR(32)`.

## Deployment

> **Kubernetes is the only supported, tested deployment path.** The Helm chart in
> [`helm/kvm-orchestrator/`](helm/kvm-orchestrator/) is how KVM Orchestrator is run and
> validated. The Docker notes below are for local development only and are
> **untested/unsupported**.

### Kubernetes (supported)

What you provide:

- A container registry hosting the three images (`api`, `ui`, `console-proxy`).
- An ingress. The chart ships an Istio Gateway + VirtualService; adapt to your
  ingress if you don't run Istio. TLS is terminated at the ingress.
- Storage. **Ceph RBD (or other shared storage) is recommended** — it lets a VM
  migrate between hosts without copying its disk. Local disks work but offline
  migration then copies the image.
- Your values file, copied from
  [`values/values.example.yaml`](helm/kvm-orchestrator/values/values.example.yaml)
  with every `CHANGE_ME` / `*.example.com` placeholder filled in.

Schema is Alembic-managed and runs as a Helm pre-install/pre-upgrade **hook**
(`templates/job-migrate.yaml`), so pods only start against an up-to-date schema.

Fresh install:

```bash
cp helm/kvm-orchestrator/values/values.example.yaml my-values.yaml
# edit my-values.yaml

helm upgrade --install kvm-orchestrator helm/kvm-orchestrator \
  -n kvm-orchestrator --create-namespace -f my-values.yaml

# Watch the migrate job before the pods come up
kubectl -n kvm-orchestrator logs job/kvm-orchestrator-migrate -f

# Health check (adjust for your ingress)
curl https://kvm-orchestrator.example.com/healthz   # expect: {"status":"ok"}
```

Upgrade (the migrate hook re-runs, applying any new revisions before rolling
pods):

```bash
helm upgrade kvm-orchestrator helm/kvm-orchestrator -n kvm-orchestrator -f my-values.yaml
```

See [`helm/kvm-orchestrator/DEPLOY.md`](helm/kvm-orchestrator/DEPLOY.md) for a fuller
runbook (data import, cutover, rotation).

### Local development with Docker (UNSUPPORTED / UNTESTED)

> ⚠️ **Not a supported deployment.** These containers have only ever been run in
> anger on Kubernetes. The steps below are a convenience starting point for
> hacking on the code locally; there is **no docker-compose file**, no CI
> coverage for this path, and you may need to adjust it to get it working. Do
> **not** run this in production.

All services are plain containers, so you *can* build and `docker run` them on a
single host for development. Bring up `mysql:8` and `redis:7` on a shared Docker
network, then run the `api`, `ui`, and `console-proxy` images against them,
passing configuration via environment variables (see
[`controller/app/config.py`](controller/app/config.py) for every supported var,
and `values.example.yaml` for a documented set). Point `DATABASE_URL` and
`REDIS_URL` at your local MySQL/Redis, set `ADMIN_PASSWORD` to log in, and leave
the `LDAP_*` vars blank to use the bootstrap admin only.

### Build & release

Each component builds from its own Dockerfile. Example reference pipelines live
alongside them (`controller/Jenkinsfile`, `frontend/Jenkinsfile`,
`console-proxy/Jenkinsfile`) — adapt the registry and credentials to your CI.

Images (retag for your registry):

| Component | Dockerfile | Image |
|---|---|---|
| API / workers | `controller/Dockerfile` | `your-registry.example.com/kvm-orchestrator/api` |
| UI | `frontend/Dockerfile` | `your-registry.example.com/kvm-orchestrator/ui` |
| Console proxy | `console-proxy/Dockerfile` | `your-registry.example.com/kvm-orchestrator/console-proxy` |

A **version-consistency gate** is enforced: all six version locations must match
before a build is accepted — `controller/app/version.py`,
`console-proxy/proxy/version.py`, `frontend/src/version.ts`, and the
`LABEL org.opencontainers.image.version` line in each of the three Dockerfiles.
The API build additionally spins up a throwaway MySQL and runs
`alembic upgrade head` end-to-end before pushing.

Build notes:
- **Node.js 20+** is required to build the UI (TailwindCSS v4's oxide engine
  needs ≥ 20).
- There is intentionally **no docker-compose**; build with `docker build`, then
  deploy via Helm.

### Database backups

All durable state lives in MySQL (Redis is ephemeral — see
[Redis Usage](#redis-usage)). Back up MySQL with your standard tooling, e.g.
`mysqldump --single-transaction --set-gtid-purged=OFF --routines --events`
against the MySQL pod, shipped to your backup store on a schedule. A reference
Jenkins pipeline is included at `database/Jenkinsfile.backup-db`.

## Troubleshooting

### Kubernetes: pods not coming up

```bash
kubectl -n kvm-orchestrator get pods
kubectl -n kvm-orchestrator describe pod <pod>       # events tell most stories
kubectl -n kvm-orchestrator logs <pod> --previous    # if crashlooping
```

| Symptom | Likely cause | Fix |
|---|---|---|
| API pods `CrashLoopBackOff` on fresh install | Migrate job hasn't finished — the probe hits `/healthz` before Alembic tables exist | Wait for the migrate job to complete: `kubectl -n kvm-orchestrator logs job/kvm-orchestrator-migrate` |
| Migrate job never runs | Helm hook didn't fire | `helm upgrade` again with the same values; the migrate hook re-runs on upgrades |
| MySQL pod `Pending` | PVC not bound — storage class/provisioner issue | `kubectl describe pvc -n kvm-orchestrator` shows the underlying reason |
| `/healthz` 200 via `port-forward` but 404 via ingress | Ingress (Gateway/VirtualService) not applied, or DNS not pointing at the ingress VIP | Check your ingress resources and DNS; use `curl --resolve` to bypass DNS while diagnosing |
| Login returns "LDAP error" | Bind creds wrong or LDAP unreachable from the cluster | Re-check the LDAP config in the **Users** tab; test a bind from an ephemeral pod |

### Kubernetes: MySQL root password rotation

`MYSQL_ROOT_PASSWORD` in the StatefulSet only applies on first init. Once the PVC
has data, only a live `ALTER USER` changes it. **Order matters** — rotate live
first, then update your values file, then `helm upgrade`:

```bash
MYSQL_POD=$(kubectl -n kvm-orchestrator get pod -l app.kubernetes.io/component=mysql -o name)
kubectl -n kvm-orchestrator exec $MYSQL_POD -- \
  mysql -u root -p<OLD_PW> -e "
    ALTER USER 'root'@'%'         IDENTIFIED BY '<NEW_PW>';
    ALTER USER 'root'@'localhost' IDENTIFIED BY '<NEW_PW>';
    FLUSH PRIVILEGES;"
# then update rootPassword in your values file + helm upgrade
```

The app DB user (from the `DATABASE_URL` secret) is separate from root.

### VM console

See [VM Console — How It Works](#vm-console--how-it-works) for the full
architecture and its troubleshooting table.

## Version History

| Version | Changes |
|---|---|
| v0.35.1 | Node agent renamed `wvc-agent` → **`kvm-orchestrator-agent`** (Python package `kvm_orchestrator_agent`, systemd unit `kvm-orchestrator-agent.service`, install paths `/opt`/`/etc/kvm-orchestrator-agent`, ansible playbook `install_kvm_orchestrator_agent.yml`). No API or metric-name changes |
| v0.35.0 | Project renamed from KVMaestro to **KVM Orchestrator** (`kvm-orchestrator`) — identity strings, brand preset/key, logo asset, Helm chart name, and all docs/comments updated. No functional change |
| v0.34.1 | Configurable per-brand user avatars — by default the sidebar shows an initial-letter avatar and never fetches from an external host; set `Brand.directoryPhotoBase` in `branding.ts` only if you run a corporate photo directory keyed by username |
| v0.34.0 | HA reliability — Readmit now also recovers nodes in `failed` status (not just `fenced`); a mass-failure circuit breaker prevents a controller-side blip from flagging the whole fleet at once; nodes flagged `failed` auto-recover to `online` once they heartbeat again (fenced nodes still require guarded admin readmit); and OVS VLAN bridges present on the nodes are auto-discovered into the networks catalog (hidden, pending admin review) |
| v0.33.1 | Dependency security patch — cryptography 44→50, Flask-Cors 5→6, marshmallow 3.23→3.26, requests 2.32→2.33, asyncssh 2.19→2.24, axios →1.20. Clears the bulk of reported CVEs; two advisories documented as accepted/deferred in [`SECURITY-NOTES.md`](SECURITY-NOTES.md). No functional changes |
| v0.33.0 | Offline-migration CPU-feature fix (stopped-VM migration across hosts with differing CPU features no longer fails on e.g. `missing features: MPX`); hard-stop/force-off action for running VMs; VM detail-page power actions bar; runtime BRANDING toggle (debranded KVM Orchestrator vs. custom branding) |
| v0.32.0 | Open-source auth: bootstrap admin account + DB-configured LDAP (admin Users tab); VM column picker; VM detail page |
| v0.30.0 | VM Lifecycle / 1-year TTL — per-VM term with owner + tenant-group warnings, one-click renew, restart-block at expiry, and an Ops delete request (never auto-deletes); admin exempt + runtime on/off |
| v0.17.0 | Kubernetes deployment via Helm — Alembic baseline migration for data-safe upgrades, unauthenticated `/healthz` for probes |
| v0.16.0 | Prometheus metrics export (`/metrics`) with Grafana dashboard |
| v0.7.1 | Live migration |
| v0.6 | Networks page, Storage page, Node detail view |
| v0.5 | Edit VM: vCPUs, RAM, disk (users); name, description, network, boot order (admins) |
| v0.1 | Initial migration tooling, nginx/gunicorn timeouts, PCI passthrough stripped |

## License

KVM Orchestrator is licensed under the **Apache License, Version 2.0**. You may
use, modify, and distribute it under the terms of that license. A full copy is
available in the [`LICENSE`](LICENSE) file and at
<https://www.apache.org/licenses/LICENSE-2.0>.

Every source file carries an SPDX identifier (`SPDX-License-Identifier: Apache-2.0`)
and the copyright notice:

```
Copyright 2026 Cisco Systems, Inc. and its affiliates
```

Unless required by applicable law or agreed to in writing, software distributed
under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
CONDITIONS OF ANY KIND, either express or implied. See the License for the
specific language governing permissions and limitations under the License.
