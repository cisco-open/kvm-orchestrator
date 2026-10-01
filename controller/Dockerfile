# docker build -t kvmaestro/api:latest .
# v0.26.6 — Fix rbd "error opening pool rbdpool" on Advanced/blank + existing-disk creates: _resolve_ceph_pool translates libvirt pool name (rbdpool) to Ceph pool (libvirt-pool) before raw rbd ops. Builds on v0.26.5 os/boot disk-template fix.
# v0.26.5 — Fix Windows/os-boot quick-create: _build_disk_xml strips cloned per-device <boot> from the disk template (clashed with os-level <boot dev>). Seen on daas-kvm-c220-04.
# v0.26.4 — Placement fix: a DEFINED-but-inactive libvirt rbd pool no longer excludes a node from require_rbd placement (kvmaestro uses raw rbd CLI, not the libvirt pool). Fixes spurious "No eligible host". Fail-open on inconclusive SSH probe.
# v0.26.3 — Deployment-error observability: VM-create failures now name the target node in the user-facing error message and log the requesting user (CEC) + node (hostname/id) at the API edge, on top of the v0.24.1 per-branch service logging. UI-only label swap (Quick Create->Create VM, Create VM->Advanced Create VM) + NodeDetail storage-pool panel from live discovery.
# v0.26.2 — Fix: could not create the first RBD VM on a freshly-added node (empty node had no existing VM to sample the Ceph disk-auth template from → empty template → rbd ran with no keyring → create failed). Discovery now synthesizes the auth template from the node's rbd pool definition (virsh pool-dumpxml) as a fallback; VM-sampling stays primary. Also surfaces netfs storage pools (previously dropped).
# v0.26.1 — Fix: VM create/attach against a non-RBD libvirt pool (a 'dir' pool such as 'default' or 'nvram') failed with the cryptic 'rbd: error opening pool: (2) No such file or directory' from Ceph, because the create paths only validated the pool NAME existed, never its TYPE, before running raw rbd commands. Added _validate_rbd_pool guard to blank + existing-disk create paths (clear rejection listing valid rbd pools); frontend filters disk-pool selectors to rbd-type only. ISO pool selectors unchanged.
# v0.26.0 — Node cordoning. Admins can mark a node non-schedulable (nodes.schedulable, alembic 0007) so it accepts no new VMs (create or migrate) while existing VMs keep running. placement.select_node excludes schedulable=0 in both passes; new admin-only POST /api/nodes/<id>/schedulable; Nodes-page cordon toggle + badge; migrate target list hides cordoned nodes. Plus migrate-modal scroll fix (UI-only).
# v0.24.1 — Observability: log every VM-create storage/virsh failure. Previously vm_create.py returned {"error": ...} to the HTTP caller on every failure branch (rbd create/clone/copy/resize, virsh define/start, pool-not-found) with NO logger call, so failures like "rbd create failed: No such file or directory" appeared in the user's 400 response but nowhere in kubectl logs. Added logger.error(node, pool, vm, rc, stderr) to all four create paths (blank/clone/existing/quick-golden).
# v0.24.0 — This session's WVC-side fixes: (1) /api/tenants and /api/networks GET now use @mcp_or_jwt_required so the self-service MCP sidecar can enumerate them; tenants also filter non-admins to SHARED, PERSONAL, plus the user's own user_tenants rows. (2) New GET /api/users/me lets the MCP resolve the impersonated CEC to a numeric owner_user_id so its concurrent-VM guardrail can count OWNED VMs (not the ~100+ visible via Binary Builders tenant membership).
# v0.23.0 — MCP impersonation: new @mcp_or_jwt_required decorator on the eight core VM endpoints (list/get/create/update/delete/start/stop/reboot) accepts requests bearing MCP_SHARED_SECRET + X-Impersonate-User header from the self-service MCP sidecar. Adds users.mcp_wvc_enabled opt-out flag (alembic 0006) and wvc_mcp_auth_total metric.

FROM python:3.11-slim

LABEL org.opencontainers.image.version="v0.33.0" \
      org.opencontainers.image.description="VM-HA v2 — human-in-the-loop failover: detect+confirm dead node (Ceph watcher partition guard), mark failed + notify (no auto-fence), admin fences via button, interlock blocks recovery until fenced, rebuild from captured XML onto arch-matched target, readmit safety pre-flight + full blocklist cleanup, admin recovery checklist."

# Prometheus multiprocess-mode shard directory. MUST be set before any
# prometheus_client Counter/Histogram is constructed (that's why it's an
# ENV, not just set inside the app). gunicorn.conf.py wipes/recreates it
# on boot and reaps dead-worker shards via child_exit.
ENV PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus_multiproc

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libmariadb-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Default: run Gunicorn (override for celery worker/beat)
EXPOSE 8000
CMD ["gunicorn", "wsgi:app", "-c", "gunicorn.conf.py", "--bind", "0.0.0.0:8000", "--workers", "4", "--timeout", "3600"]
