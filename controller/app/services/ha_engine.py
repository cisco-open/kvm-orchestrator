"""HA Engine — failure detection and VM relocation orchestration.

This is the piece that makes HA "live": the celery-beat loop calls
``check_node_heartbeats`` every 15s. It ties together detection
(stale heartbeat) with the fence-then-restart mechanics in
``ha_service``.

SAFETY:
  * Every action is gated on ``ha_service.is_ha_armed()`` — env master
    arm AND the Redis runtime kill-switch. When NOT armed, the engine
    runs in DETECT-ONLY mode: it logs an ha_events row for a detected
    dead node (trigger='heartbeat_timeout', result='detected_only') and
    does NOTHING else. No fence, no relocate.
  * Invariant: FENCE FIRST. relocate_vms fences the dead node before
    touching any VM, and ABORTS the whole failover if fencing fails.
  * A node already marked 'fenced' is skipped (no double-failover).
"""

import logging
import os
from datetime import datetime, timedelta
from urllib.parse import urlparse

import pymysql
import pymysql.cursors

from . import ha_service
from . import placement

logger = logging.getLogger("wvc.ha-engine")


def _get_db():
    url = urlparse(os.environ.get("DATABASE_URL", ""))
    return pymysql.connect(
        host=url.hostname, port=url.port or 3306,
        user=url.username, password=url.password,
        database=url.path.lstrip("/"),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def _failover_threshold() -> int:
    try:
        from flask import current_app
        return int(current_app.config.get("HA_FAILOVER_THRESHOLD", 120))
    except Exception:
        return int(os.environ.get("HA_FAILOVER_THRESHOLD", 120))


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def check_node_heartbeats() -> None:
    """Celery-beat entrypoint (every 15s). Find HA-eligible nodes whose
    last_heartbeat_at is stale beyond the failover threshold and are not
    already fenced, then drive failover for each.

    Runs whether or not HA is armed — detection + logging always happen;
    the ACTION (fence/relocate) inside relocate_vms is what's gated.
    """
    threshold = _failover_threshold()
    cutoff = datetime.utcnow() - timedelta(seconds=threshold)

    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, hostname, ip_address, status, last_heartbeat_at
                FROM nodes
                WHERE ha_enabled = 1
                  AND status NOT IN ('fenced', 'failed')
                  AND last_heartbeat_at IS NOT NULL
                  AND last_heartbeat_at < %s
                """,
                (cutoff,),
            )
            dead = cur.fetchall()
    finally:
        conn.close()

    if not dead:
        return

    armed = ha_service.is_ha_armed()
    for node in dead:
        age = (datetime.utcnow() - node["last_heartbeat_at"]).total_seconds()
        if not armed:
            # DETECT-ONLY: log once per detection and move on. No action.
            logger.warning(
                "HA detect-only: node %s (%s) heartbeat stale %.0fs (threshold "
                "%ds) — HA NOT armed, taking no action",
                node["hostname"], node["ip_address"], age, threshold,
            )
            ha_service.record_ha_event(
                node_id=node["id"], trigger="heartbeat_timeout",
                result="detected_only",
                detail={"stale_seconds": int(age), "threshold": threshold,
                        "armed": False},
            )
            continue

        # ARMED. We still do NOT auto-fence. Fencing is a destructive action
        # (blocklists the node off Ceph) and is an ADMIN decision via the HA
        # page 'Fence' button. The engine's job when armed is to CONFIRM the
        # failure and flag the node as 'failed' (needs fence) so the admin can
        # act. The interlock in the recovery path then refuses to migrate a VM
        # off the node until it is fenced.
        logger.error(
            "HA ARMED: node %s (%s) heartbeat stale %.0fs > %ds — confirming "
            "before flagging failed",
            node["hostname"], node["ip_address"], age, threshold,
        )

        # CONFIRMATION: a stale heartbeat alone is NOT proof the host is dead —
        # a management-network partition looks identical while the VMs keep
        # running fine. Ask Ceph (independent, storage-side truth): is the node
        # still holding RBD watchers (still writing its disks)? If yes it's a
        # partition, not a failure — do not flag it failed; alert instead.
        survivor = ha_service._pick_survivor(node["id"])
        if survivor:
            probe = ha_service.node_still_writing_ceph(node, survivor)
            if probe.get("writing"):
                logger.error(
                    "HA: node %s looks stale but is STILL WRITING Ceph (%d image"
                    "(s)) — SUSPECTED MGMT PARTITION, not flagging failed. images=%s",
                    node["hostname"], len(probe["images_watched"]),
                    probe["images_watched"],
                )
                ha_service.record_ha_event(
                    node_id=node["id"], trigger="heartbeat_timeout",
                    result="partition_suspected",
                    detail={"stale_seconds": int(age),
                            "images_watched": probe["images_watched"],
                            "note": "node unreachable on mgmt but still writing "
                                    "Ceph — not fenced (false-positive guard)"},
                )
                try:
                    from . import ha_notify
                    # Throttle the Webex alert: the heartbeat check runs every
                    # ~15s and a partition can persist for a long time, so
                    # notifying every cycle spams the room. Send at most once
                    # per hour per node (Redis key with TTL). The log line +
                    # ha_event above are still written every cycle for audit.
                    _throttled = False
                    try:
                        from .ha_service import _redis
                        _k = f"ha:partition_alerted:{node['id']}"
                        # SET NX EX 3600 → succeeds only if not already set in
                        # the last hour; that first success is when we notify.
                        if _redis().set(_k, "1", nx=True, ex=3600):
                            _throttled = False
                        else:
                            _throttled = True
                    except Exception:
                        _throttled = False  # Redis blip → don't suppress alerts
                    if not _throttled:
                        ha_notify.notify_partition(node["hostname"], probe)
                except Exception:
                    logger.exception("partition notify failed (non-fatal)")
                continue

        # Confirmed unreachable AND not writing Ceph → flag as FAILED (needs an
        # admin to fence). Do NOT fence here. Idempotent: skip if already failed
        # or fenced (avoids duplicate notifications each cycle).
        if node.get("status") in ("failed", "fenced"):
            continue
        logger.error(
            "HA ARMED: node %s (%s) confirmed dead — flagging FAILED (awaiting "
            "admin fence via HA page)",
            node["hostname"], node["ip_address"],
        )
        ha_service.record_ha_event(
            node_id=node["id"], trigger="heartbeat_timeout", result="failed",
            detail={"stale_seconds": int(age),
                    "note": "node confirmed dead — admin must fence it on the "
                            "HA page before its VMs can be recovered"},
        )
        _mark_node_status(node["id"], "failed")
        try:
            from . import ha_notify
            ha_notify.notify_node_failed(node["hostname"], node["id"])
        except Exception:
            logger.exception("node-failed notify failed (non-fatal)")


# ---------------------------------------------------------------------------
# Failover orchestration
# ---------------------------------------------------------------------------

def _vms_on_node(node_id: int) -> list[dict]:
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, node_id, state, vcpus, ram_mb, disk_gb,
                       tenant_id, disk_path, libvirt_xml,
                       last_running_at,
                       TIMESTAMPDIFF(SECOND, last_running_at, UTC_TIMESTAMP())
                           AS running_age_s
                FROM vms WHERE node_id = %s
                ORDER BY ram_mb DESC, vcpus DESC
                """,
                (node_id,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def _mark_node_status(node_id: int, status: str) -> None:
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE nodes SET status = %s WHERE id = %s",
                        (status, node_id))
        conn.commit()
    finally:
        conn.close()


def relocate_vms(failed_node_id: int, trigger: str = "manual") -> dict:
    """Fence the failed node, then relocate its HA-eligible VMs onto
    survivors. FENCE FIRST — abort everything if the fence fails.

    Callable directly for an admin-forced failover (trigger='manual').
    Requires HA to be armed; refuses otherwise (the safety gate is here
    too, not only in the detector, so the manual API path is also gated).
    """
    if not ha_service.is_ha_armed():
        return {"error": "HA is not armed (env master-arm + runtime "
                         "kill-switch must both be on) — refusing to fence/relocate"}

    dead = ha_service._get_node(failed_node_id)
    if not dead:
        return {"error": f"node {failed_node_id} not found"}
    if dead["status"] == "fenced":
        return {"error": f"node {dead['hostname']} already fenced — "
                         "use readmit to recover it"}

    survivor = ha_service._pick_survivor(failed_node_id)
    if not survivor:
        msg = "no online survivor available to fence from / relocate to"
        logger.error("HA failover %s: %s", dead["hostname"], msg)
        ha_service.record_ha_event(
            node_id=failed_node_id, trigger=trigger, result="failed",
            detail={"error": msg})
        return {"error": msg}

    vms = _vms_on_node(failed_node_id)

    # --- FENCE FIRST (unconditional; abort on failure) ---
    fence_res = ha_service.fence_node(dead, survivor)
    if "error" in fence_res:
        logger.error("HA failover %s ABORTED — fence failed: %s",
                     dead["hostname"], fence_res["error"])
        ha_service.record_ha_event(
            node_id=failed_node_id, trigger=trigger, result="fence_failed",
            source_node_id=failed_node_id, target_node_id=survivor["id"],
            detail={"error": fence_res["error"],
                    "detail": fence_res.get("detail")})
        return {"error": f"fence failed, failover aborted: {fence_res['error']}"}

    fence_detail = fence_res.get("detail", {})
    # Mark fenced so placement excludes it and we don't double-failover.
    _mark_node_status(failed_node_id, "fenced")
    ha_service.record_ha_event(
        node_id=failed_node_id, trigger=trigger, result="fenced",
        source_node_id=failed_node_id, target_node_id=survivor["id"],
        detail=fence_detail)

    # --- Identify affected VMs and NOTIFY OPS (do NOT auto-relocate) ---
    #
    # We deliberately do NOT auto-relocate. Auto-relocation cannot safely pick a
    # target: the VM's XML may pin a specific CPU model / machine type (e.g.
    # SapphireRapids + a noble machine type) that only some survivors support,
    # and encoding that compatibility logic here would duplicate what the
    # manual migrate path (vm_service.vm_migrate) already does. Instead the
    # engine fences the dead host (making its storage safe) and hands off to
    # Ops, who migrate each VM via the UI — which already does architecture
    # matching and offline-migrate mechanics.
    #
    # A VM is reported as "needs migration" if it was POWERED ON at failure
    # time (running/paused now, or 'unknown' but observed running recently).
    # Deliberately-stopped VMs are left down.
    threshold = _failover_threshold()
    running_fresh_window = max(threshold * 3, 600)  # seconds
    affected = []   # were running — Ops should migrate these
    left_down = []  # were not running — intentionally left down
    for vm in vms:
        state = vm["state"]
        was_running_recently = (
            vm.get("running_age_s") is not None
            and vm["running_age_s"] <= running_fresh_window
        )
        if state in ("running", "paused") or (state == "unknown" and was_running_recently):
            affected.append({"vm": vm["name"], "vm_id": vm["id"],
                             "state": state, "has_xml": bool(vm.get("libvirt_xml"))})
        else:
            left_down.append({"vm": vm["name"], "reason": f"not running ({state})"})

    results = {"fenced": True, "affected": affected, "left_down": left_down}

    # Log a per-VM ha_event for the audit trail (action needed).
    for a in affected:
        ha_service.record_ha_event(
            node_id=failed_node_id, vm_id=a["vm_id"], trigger=trigger,
            result="needs_migration", source_node_id=failed_node_id,
            detail={"state": a["state"], "note": "host fenced — migrate via UI"})

    logger.warning(
        "HA failover %s: fenced, %d VM(s) need migration by Ops, %d left down",
        dead["hostname"], len(affected), len(left_down))

    # Notify (Webex). Never let a notification failure affect the failover.
    try:
        from . import ha_notify
        ha_notify.notify_failover(dead["hostname"], results)
    except Exception:
        logger.exception("HA notify failed (non-fatal)")

    return {"ok": True, **results}


def readmit_node(node_id: int) -> dict:
    """Guarded recovery of a fenced node (admin-initiated). Thin wrapper
    around ha_service.readmit_node with survivor selection."""
    node = ha_service._get_node(node_id)
    if not node:
        return {"error": f"node {node_id} not found"}
    survivor = ha_service._pick_survivor(node_id)
    if not survivor:
        return {"error": "no online survivor to run the readmission checks from"}
    return ha_service.readmit_node(node, survivor)


def readmit_check(node_id: int) -> dict:
    """READ-ONLY pre-flight: is it safe to readmit this fenced node?"""
    node = ha_service._get_node(node_id)
    if not node:
        return {"error": f"node {node_id} not found"}
    survivor = ha_service._pick_survivor(node_id)
    if not survivor:
        return {"error": "no online survivor to run the readmission checks from"}
    return ha_service.readmit_check(node, survivor)


def admin_fence_node(node_id: int, force: bool = False) -> dict:
    """Admin-initiated manual fence (the HA-page 'Fence' button).

    Fences a node the admin has confirmed is dead: Ceph-blocklists it so it
    cannot touch shared storage, then marks it 'fenced' so its VMs can be
    recovered. This is the human authorisation the engine deliberately does NOT
    do automatically.

    Safety: re-runs the partition guard first — if the node is STILL writing
    Ceph (mgmt partition, VMs alive), refuses unless force=True, because fencing
    would crash healthy VMs. force lets the admin override when they know the
    host is truly dead.
    """
    node = ha_service._get_node(node_id)
    if not node:
        return {"error": f"node {node_id} not found"}
    if node.get("status") == "fenced":
        return {"error": f"node {node['hostname']} is already fenced"}

    survivor = ha_service._pick_survivor(node_id)
    if not survivor:
        return {"error": "no online survivor available to fence from"}

    # Partition guard (unless overridden): don't fence a node whose VMs are
    # provably still writing Ceph.
    if not force:
        probe = ha_service.node_still_writing_ceph(node, survivor)
        if probe.get("writing"):
            return {
                "error": "REFUSED — node is still writing Ceph (its VMs appear "
                         "ALIVE). This looks like a management-network partition, "
                         "not a dead host. Fencing would crash those VMs. If you "
                         "are certain the host is dead, retry with force.",
                "partition_suspected": True,
                "images_watched": probe.get("images_watched", []),
            }

    fence_res = ha_service.fence_node(node, survivor)
    if "error" in fence_res:
        ha_service.record_ha_event(
            node_id=node_id, trigger="manual", result="fence_failed",
            detail={"error": fence_res["error"]})
        return {"error": f"fence failed: {fence_res['error']}"}

    _mark_node_status(node_id, "fenced")
    ha_service.record_ha_event(
        node_id=node_id, trigger="manual", result="fenced",
        detail={**fence_res.get("detail", {}), "forced": bool(force)})
    return {"ok": True, "hostname": node["hostname"],
            "detail": fence_res.get("detail", {})}
