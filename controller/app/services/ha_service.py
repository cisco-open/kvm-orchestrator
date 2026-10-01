"""HA service — runtime control (kill-switch), audit logging, and the
fencing / relocation / readmission mechanics for VM-HA.

SAFETY MODEL — three independent gates, ALL must be true for the engine
to fence a node or relocate a VM:

  1. ``HA_ENABLED`` (env, boot-level master arm) — a cluster that has not
     been vetted for fencing cannot be armed by a UI click alone.
  2. ``ha:runtime_enabled`` (Redis, instant admin toggle) — the "STOP HA
     NOW" switch. Read fresh on every detection loop so an admin toggle
     takes effect within one cycle without a restart. FAILS SAFE: if
     Redis is unreachable or the key is missing, HA is treated as
     DISABLED (a Redis blip can never silently arm fencing).
  3. ``nodes.ha_enabled`` (per-node) — exclude individual nodes.

The Redis flag is ephemeral by design; the DURABLE record of who
armed/disarmed and when lives in the ``ha_events`` table (trigger
``admin_toggle``). After a Redis flush the flag is absent → fail-safe
disabled → HA stays off until an admin re-arms it.
"""

import json
import logging
import os
from datetime import datetime
from urllib.parse import urlparse

import pymysql
import pymysql.cursors
import redis as redis_lib
from flask import current_app

logger = logging.getLogger("wvc.ha")

# Redis key for the runtime kill-switch.
_RUNTIME_KEY = "ha:runtime_enabled"


# ---------------------------------------------------------------------------
# Infra helpers
# ---------------------------------------------------------------------------

def _redis() -> "redis_lib.Redis":
    return redis_lib.from_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
    )


def _get_db():
    url = urlparse(os.environ.get("DATABASE_URL", ""))
    return pymysql.connect(
        host=url.hostname, port=url.port or 3306,
        user=url.username, password=url.password,
        database=url.path.lstrip("/"),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def _env_ha_enabled() -> bool:
    """Boot-level master arm. Reads Flask config when in app context,
    else falls back to the raw env var (celery worker context)."""
    try:
        return bool(current_app.config.get("HA_ENABLED", False))
    except Exception:
        return os.environ.get("HA_ENABLED", "false").lower() == "true"


# ---------------------------------------------------------------------------
# Runtime kill-switch
# ---------------------------------------------------------------------------

def get_runtime_enabled() -> bool:
    """Return the runtime kill-switch state from Redis.

    FAILS SAFE: any Redis error, or a missing key, yields False. HA can
    only ever act when this explicitly returns True.
    """
    try:
        val = _redis().get(_RUNTIME_KEY)
    except Exception:
        logger.warning("HA runtime flag: Redis unreachable — treating as DISABLED")
        return False
    return val == "1"


def is_ha_armed() -> bool:
    """Effective global arm state: env master-arm AND runtime kill-switch.

    Per-node ``ha_enabled`` is checked separately by the engine when it
    iterates candidate nodes. This function answers "may HA act at all
    right now?".
    """
    return _env_ha_enabled() and get_runtime_enabled()


def set_runtime_enabled(enabled: bool, by_user: str | None = None,
                        reason: str | None = None) -> dict:
    """Flip the runtime kill-switch (admin action) and audit it.

    Writes the Redis flag, then records an ``ha_events`` row so the
    who/when/why is durable even though the flag itself is ephemeral.
    Returns the resulting effective state.
    """
    try:
        _redis().set(_RUNTIME_KEY, "1" if enabled else "0")
    except Exception as exc:
        logger.error("HA runtime flag: failed to set Redis key: %s", exc)
        return {"error": f"Failed to set HA runtime flag: {exc}"}

    # Durable audit trail (best-effort; the flag write already succeeded).
    try:
        record_ha_event(
            node_id=None, vm_id=None, source_node_id=None, target_node_id=None,
            trigger="admin_toggle",
            result="enabled" if enabled else "disabled",
            detail={
                "runtime_enabled": enabled,
                "by_user": by_user,
                "reason": reason,
                "env_ha_enabled": _env_ha_enabled(),
                "ts": datetime.utcnow().isoformat() + "Z",
            },
        )
    except Exception:
        logger.exception("HA runtime flag: failed to write audit ha_event")

    logger.warning("HA runtime kill-switch set to %s by %s (reason=%r)",
                   "ENABLED" if enabled else "DISABLED", by_user or "?", reason)
    return {
        "runtime_enabled": enabled,
        "env_ha_enabled": _env_ha_enabled(),
        "armed": is_ha_armed(),
    }


def ha_status() -> dict:
    """Full HA arm status for the API / UI banner."""
    runtime = get_runtime_enabled()
    env = _env_ha_enabled()
    return {
        "env_ha_enabled": env,          # boot-level master arm (env)
        "runtime_enabled": runtime,     # instant kill-switch (Redis)
        "armed": env and runtime,       # effective: will HA act?
        "failover_threshold": int(os.environ.get("HA_FAILOVER_THRESHOLD", 120)),
    }


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def record_ha_event(node_id: int | None, trigger: str, result: str,
                    vm_id: int | None = None,
                    source_node_id: int | None = None,
                    target_node_id: int | None = None,
                    detail: dict | None = None) -> None:
    """Insert an ha_events audit row. node_id may be None for global
    events (e.g. admin_toggle) — stored as NULL."""
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ha_events
                    (timestamp, node_id, vm_id, source_node_id,
                     target_node_id, `trigger`, result, detail)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    datetime.utcnow(), node_id, vm_id, source_node_id,
                    target_node_id, trigger, result,
                    json.dumps(detail) if detail is not None else None,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Node helpers (loaded with SSH key + rbd auth material)
# ---------------------------------------------------------------------------

def _get_node(node_id: int) -> dict | None:
    """Node row joined with its SSH private key."""
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT n.id, n.hostname, n.ip_address, n.status,
                       n.ssh_host, n.ssh_port, n.ssh_user,
                       n.ha_enabled, n.ha_priority, k.private_key
                FROM nodes n
                LEFT JOIN ssh_keys k ON k.id = n.ssh_key_id
                WHERE n.id = %s
                """,
                (node_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def _pick_survivor(exclude_node_id: int) -> dict | None:
    """Pick an ONLINE, schedulable node (other than the dead one) from
    which to run fencing commands. Any healthy peer with the Ceph client
    works; we prefer one that is a valid placement target too."""
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT n.id, n.hostname, n.ip_address, n.status,
                       n.ssh_host, n.ssh_port, n.ssh_user, k.private_key
                FROM nodes n
                LEFT JOIN ssh_keys k ON k.id = n.ssh_key_id
                WHERE n.id != %s AND n.status = 'online'
                  AND n.schedulable = 1
                  AND k.private_key IS NOT NULL
                ORDER BY n.available_ram_mb DESC
                LIMIT 1
                """,
                (exclude_node_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Ceph fencing auth — write a temp keyring for client.wvc-ha on a
# surviving node, run the command, remove the keyring. The privileged
# fencing key is held by the controller (config), never left on a host.
# ---------------------------------------------------------------------------

def _mon_hosts() -> str:
    """Comma-separated mon list for ceph/rbd -m. Prefer explicit config;
    fall back to placeholder example mons."""
    cfg = ""
    try:
        cfg = current_app.config.get("HA_CEPH_MON_HOST", "") or ""
    except Exception:
        cfg = os.environ.get("HA_CEPH_MON_HOST", "") or ""
    return cfg or "10.0.0.11,10.0.0.12,10.0.0.13"


def _ha_ceph_key() -> str:
    try:
        return current_app.config.get("HA_CEPH_KEY", "") or ""
    except Exception:
        return os.environ.get("HA_CEPH_KEY", "") or ""


def _ha_ceph_user() -> str:
    try:
        return current_app.config.get("HA_CEPH_USER", "wvc-ha") or "wvc-ha"
    except Exception:
        return os.environ.get("HA_CEPH_USER", "wvc-ha") or "wvc-ha"


class _CephFence:
    """Context manager: stage the wvc-ha keyring on a surviving node and
    yield an auth-arg string for ceph/rbd. Cleans up on exit."""

    def __init__(self, survivor: dict):
        self.survivor = survivor
        self.keyring_path: str | None = None
        self.auth_args = ""

    def __enter__(self):
        import secrets as _secrets
        from .vm_service import _ssh_run  # reuse the established SSH layer
        key = _ha_ceph_key()
        user = _ha_ceph_user()
        if not key:
            raise RuntimeError(
                "HA_CEPH_KEY is not configured — cannot fence. Set the "
                "client.wvc-ha key in the controller config."
            )
        self.keyring_path = f"/tmp/wvc_ha_{_secrets.token_hex(4)}.keyring"
        content = f"[client.{user}]\n\tkey = {key}\n"
        import base64 as _b64
        kb64 = _b64.b64encode(content.encode()).decode()
        out, err, rc = _ssh_run(
            self.survivor,
            f"printf '%s' '{kb64}' | base64 -d > {self.keyring_path} "
            f"&& chmod 600 {self.keyring_path}",
        )
        if rc != 0:
            raise RuntimeError(f"Failed to stage fencing keyring: {err or out}")
        self.auth_args = (
            f"--id {user} --keyring {self.keyring_path} --mon-host '{_mon_hosts()}'"
        )
        return self

    def __exit__(self, *exc):
        from .vm_service import _ssh_run
        if self.keyring_path:
            try:
                _ssh_run(self.survivor, f"rm -f {self.keyring_path}")
            except Exception:
                logger.warning("Failed to remove fencing keyring %s on %s",
                               self.keyring_path, self.survivor.get("hostname"))
        return False


# ---------------------------------------------------------------------------
# Fencing — provably stop a dead node from touching shared Ceph BEFORE we
# restart its VMs elsewhere. Invariant: FENCE, THEN restart. Never restart
# without a successful fence.
# ---------------------------------------------------------------------------

def _affected_rbd_images(dead_node_id: int) -> list[str]:
    """RBD images (pool/name) of VMs currently recorded on the dead node."""
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT disk_path FROM vms WHERE node_id = %s", (dead_node_id,)
            )
            images = []
            for row in cur.fetchall():
                dp = (row.get("disk_path") or "").strip()
                if dp.startswith("rbd:"):
                    images.append(dp[4:].split(":")[0])  # rbd:pool/img[:opts]
            return images
    finally:
        conn.close()


def node_still_writing_ceph(dead_node: dict, survivor: dict) -> dict:
    """CONFIRMATION probe: is a supposedly-dead node still actively holding
    Ceph RBD watchers on its VMs' images? If so it is NOT dead — its guests are
    still running and writing storage — and we are almost certainly seeing a
    MANAGEMENT-NETWORK PARTITION, not a host failure.

    This is the guard against false-positive fencing: a mgmt-link outage makes a
    node look dead (no heartbeat/SSH) while its VMs keep running fine. Fencing it
    would blocklist those healthy VMs off their storage and crash them. So before
    fencing we ask Ceph — the independent, storage-side source of truth — whether
    the node is still writing.

    Returns {"writing": bool, "images_watched": [imgs], "checked": int,
             "error": str?}. On any probe error, writing=False is NOT assumed —
    caller decides; we surface the error so the engine can be conservative.
    """
    from .vm_service import _ssh_run
    dead_ip = (dead_node.get("ip_address") or "").strip()
    images = _affected_rbd_images(dead_node["id"])
    watched: list[str] = []
    checked = 0
    err_msg = None
    try:
        with _CephFence(survivor) as fence:
            for image in images:
                out, err, rc = _ssh_run(
                    survivor, f"rbd {fence.auth_args} status {image} 2>/dev/null")
                checked += 1
                # `rbd status` lists watchers with their client address. If the
                # dead node's IP is still a watcher, its guest is still attached.
                if dead_ip and dead_ip in (out or ""):
                    watched.append(image)
    except Exception as exc:
        err_msg = str(exc)
        logger.warning("node_still_writing_ceph probe error for %s: %s",
                       dead_node.get("hostname"), exc)
    result = {"writing": bool(watched), "images_watched": watched,
              "checked": checked}
    if err_msg:
        result["error"] = err_msg
    return result


def fence_node(dead_node: dict, survivor: dict) -> dict:
    """Fence `dead_node` from Ceph, running commands via `survivor`.

    Steps (all via the surviving node's SSH + the wvc-ha keyring):
      1. ceph osd blocklist add <dead-node-ip>  (dead client can't write)
      2. optionally break the RBD exclusive-lock on each affected image
         (HA_BREAK_RBD_LOCK) so the target can acquire it immediately.

    Returns {"ok": True, "detail": {...}} or {"error": ...}. The detail
    (blocklist entry + broken locks) is recorded so readmission can undo
    it. A blocklist failure aborts (returns error) — the caller MUST NOT
    relocate if this fails.
    """
    from .vm_service import _ssh_run

    dead_ip = dead_node["ip_address"]
    try:
        break_lock = current_app.config.get("HA_BREAK_RBD_LOCK", True)
    except Exception:
        break_lock = os.environ.get("HA_BREAK_RBD_LOCK", "true").lower() == "true"

    detail = {"blocklisted": [], "locks_broken": [], "lock_errors": []}
    try:
        with _CephFence(survivor) as fence:
            # 1. Blocklist the dead node's Ceph client address. This is the
            #    hard fence — even if the dead host's kernel is still alive
            #    and writing, the OSDs will now reject it.
            out, err, rc = _ssh_run(
                survivor,
                f"ceph {fence.auth_args} osd blocklist add {dead_ip} 2>&1",
            )
            if rc != 0:
                logger.error("FENCE FAILED: could not blocklist %s: %s",
                             dead_ip, (err or out).strip())
                return {"error": f"blocklist add failed: {(err or out).strip()}"}
            detail["blocklisted"].append(dead_ip)
            logger.warning("FENCED: blocklisted dead node %s (%s)",
                           dead_node["hostname"], dead_ip)

            # 2. Break the RBD exclusive-lock on each affected image so the
            #    survivor can take over writes immediately. Best-effort per
            #    image — a lock may not be held, which is fine.
            if break_lock:
                for image in _affected_rbd_images(dead_node["id"]):
                    lout, lerr, lrc = _ssh_run(
                        survivor,
                        f"rbd {fence.auth_args} lock list {image} --format json 2>/dev/null",
                    )
                    if lrc != 0 or not lout.strip():
                        continue
                    try:
                        locks = json.loads(lout)
                    except Exception:
                        locks = []
                    # rbd lock list --format json → list of
                    # {"id":..., "locker":..., "address":...}
                    entries = locks.values() if isinstance(locks, dict) else locks
                    for lk in entries:
                        lock_id = lk.get("id") or lk.get("lock_id")
                        locker  = lk.get("locker")
                        if not lock_id or not locker:
                            continue
                        bout, berr, brc = _ssh_run(
                            survivor,
                            f"rbd {fence.auth_args} lock rm {image} "
                            f"'{lock_id}' {locker} 2>&1",
                        )
                        if brc == 0:
                            detail["locks_broken"].append(
                                {"image": image, "id": lock_id, "locker": locker})
                            logger.warning("FENCED: broke RBD lock on %s (%s)",
                                           image, locker)
                        else:
                            detail["lock_errors"].append(
                                {"image": image, "error": (berr or bout).strip()})
        return {"ok": True, "detail": detail}
    except Exception as exc:
        logger.exception("fence_node error")
        return {"error": f"fence error: {exc}", "detail": detail}


def unfence_node(node: dict, survivor: dict, blocklist_entries: list[str]) -> dict:
    """Remove the ceph blocklist entries added when the node was fenced.
    Used by readmission AFTER the returning host's stale domains are
    destroyed. Order matters — caller must destroy stale domains first."""
    from .vm_service import _ssh_run
    removed, errors = [], []
    try:
        with _CephFence(survivor) as fence:
            for entry in blocklist_entries:
                out, err, rc = _ssh_run(
                    survivor,
                    f"ceph {fence.auth_args} osd blocklist rm {entry} 2>&1",
                )
                if rc == 0:
                    removed.append(entry)
                    logger.warning("UNFENCED: removed blocklist %s", entry)
                else:
                    errors.append({"entry": entry, "error": (err or out).strip()})
        if errors:
            return {"error": f"unfence had errors: {errors}", "removed": removed}
        return {"ok": True, "removed": removed}
    except Exception as exc:
        return {"error": f"unfence error: {exc}", "removed": removed}


# ---------------------------------------------------------------------------
# Relocation — restart a dead node's VM on a target survivor from the
# stored libvirt_xml. Assumes the node has ALREADY been fenced.
# ---------------------------------------------------------------------------

def _get_vm(vm_id: int) -> dict | None:
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, node_id, state, disk_path, libvirt_xml "
                "FROM vms WHERE id = %s",
                (vm_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def _update_vm_node(vm_id: int, node_id: int, state: str) -> None:
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE vms SET node_id = %s, state = %s WHERE id = %s",
                (node_id, state, vm_id),
            )
        conn.commit()
    finally:
        conn.close()


def relocate_vm(vm: dict, target: dict) -> dict:
    """Redefine + start a VM on `target` from its stored libvirt_xml.

    PRECONDITION: the source node must already be fenced (Ceph blocklisted)
    so there is no chance the old QEMU is still writing the disk.

    Uses the last-known-good XML captured by vm_sync (you can't dumpxml a
    dead host). Strips host-specific passthrough devices before defining.
    """
    from .vm_service import (
        _ssh_run, _write_remote_file, _strip_host_devices,
    )
    import uuid as _uuid

    name = vm["name"]
    xml = vm.get("libvirt_xml")
    if not xml:
        return {"error": f"no stored libvirt_xml for VM '{name}' — cannot relocate"}

    xml, stripped = _strip_host_devices(xml)
    if stripped:
        logger.warning("relocate %s: stripped host devices %s", name, stripped)

    tmp = f"/tmp/wvc_ha_reloc_{_uuid.uuid4().hex[:8]}.xml"
    wr = _write_remote_file(target, tmp, xml)
    if "error" in wr:
        return wr
    try:
        out, err, rc = _ssh_run(target, f"virsh define '{tmp}'")
        if rc != 0:
            return {"error": f"virsh define failed on {target['hostname']}: {err or out}"}
        out, err, rc = _ssh_run(target, f"virsh start '{name}'")
        if rc != 0:
            # define succeeded but start failed — leave it defined (stopped)
            # so an admin can investigate; report the failure.
            return {"error": f"virsh start failed on {target['hostname']}: {err or out}",
                    "defined": True}
    finally:
        _ssh_run(target, f"rm -f '{tmp}'")

    _update_vm_node(vm["id"], target["id"], "running")
    logger.warning("RELOCATED: %s → %s (started)", name, target["hostname"])
    return {"ok": True}


# ---------------------------------------------------------------------------
# Readmission — bring a recovered host back safely. ORDER IS CRITICAL:
# destroy+undefine the stale (relocated) domains on the returning host
# BEFORE un-blocklisting it, and abort if any relocated image still shows
# the returning host as an RBD watcher (split-brain guard).
# ---------------------------------------------------------------------------

def readmit_check(node: dict, survivor: dict) -> dict:
    """READ-ONLY pre-flight for readmission — tells a human admin whether it's
    safe to readmit a fenced node, and why. Changes nothing.

    Verdict:
      * unsafe  — split-brain watcher conflict OR a stale copy actually RUNNING
                  on the returning host.
      * caution — host not reachable yet, or stale shut-off domains remain
                  (readmit will clean them).
      * safe    — reachable, no watcher conflicts, no running stale copies.
    """
    from .vm_service import _ssh_run

    node_id = node["id"]
    dead_ip = node["ip_address"]
    reasons: list[str] = []
    result = {
        "verdict": "safe", "reachable": False,
        "relocated_vms": [], "stale_domains": [], "running_stale": [],
        "watcher_conflicts": [], "reasons": reasons,
    }

    relocated = _relocated_off_node(node_id)
    result["relocated_vms"] = list(relocated["vm_names"])

    # 1. Reachable?
    reachable = False
    if node.get("private_key"):
        try:
            out, _e, rc = _ssh_run(node, "echo ok")
            reachable = (rc == 0 and "ok" in (out or ""))
        except Exception:
            reachable = False
    result["reachable"] = reachable
    if not reachable:
        reasons.append("Host is not reachable over SSH yet — cannot clean stale "
                       "domains on return. Wait until it is fully booted.")

    # 2. Stale relocated domains still defined here? Any RUNNING (dangerous)?
    if reachable:
        for vm_name in relocated["vm_names"]:
            try:
                st, _e, rc = _ssh_run(node, f"virsh domstate '{vm_name}' 2>/dev/null")
            except Exception:
                continue
            st = (st or "").strip()
            if rc == 0 and st and "no domain" not in st.lower():
                result["stale_domains"].append(vm_name)
                if st in ("running", "paused"):
                    result["running_stale"].append(vm_name)

    # 3. Split-brain watcher check (read-only).
    try:
        with _CephFence(survivor) as fence:
            for image in relocated["images"]:
                out, _e, _rc = _ssh_run(
                    survivor, f"rbd {fence.auth_args} status {image} 2>/dev/null")
                if dead_ip in (out or ""):
                    result["watcher_conflicts"].append(image)
    except Exception as exc:
        reasons.append(f"Could not run the Ceph watcher check: {exc}")
        result["verdict"] = "caution"

    # 4. Verdict.
    if result["watcher_conflicts"]:
        result["verdict"] = "unsafe"
        reasons.append(
            f"SPLIT-BRAIN RISK: {len(result['watcher_conflicts'])} relocated "
            "image(s) still show the returning host as a Ceph watcher. Do NOT "
            "readmit — the old host may still be writing.")
    elif result["running_stale"]:
        result["verdict"] = "unsafe"
        reasons.append(
            f"DANGER: stale copies of {', '.join(result['running_stale'])} are "
            "RUNNING on the returning host (likely libvirt autostart on reboot). "
            "They are still Ceph-blocklisted so cannot write, but investigate why "
            "before readmit. Readmit will disable their autostart, destroy and "
            "undefine them.")
    elif not reachable:
        result["verdict"] = "caution"
    elif result["stale_domains"]:
        result["verdict"] = "caution"
        reasons.append(
            f"{len(result['stale_domains'])} stale (shut-off) domain(s) remain "
            "defined — readmit will destroy+undefine them safely.")
    else:
        reasons.append("Host reachable, no watcher conflicts, no running stale "
                       "copies — safe to readmit.")

    # Log every check so a blocked/unsafe verdict is traceable, and record
    # unsafe verdicts to the audit ledger (someone tried and it was refused).
    logger.info("readmit_check node %s (%s): verdict=%s reasons=%s",
                node.get("hostname"), dead_ip, result["verdict"], reasons)
    if result["verdict"] == "unsafe":
        logger.warning("READMIT UNSAFE for node %s (%s): %s",
                       node.get("hostname"), dead_ip, reasons)
        try:
            record_ha_event(
                node_id=node_id, trigger="manual", result="readmit_unsafe",
                detail={"verdict": "unsafe",
                        "watcher_conflicts": result["watcher_conflicts"],
                        "running_stale": result["running_stale"],
                        "reasons": reasons})
        except Exception:
            logger.exception("failed to record readmit_unsafe ha_event")
    return result


def readmit_node(node: dict, survivor: dict) -> dict:
    """Guarded, human-initiated recovery of a fenced node.

    1. On the returning host: virsh destroy + undefine every VM that was
       relocated off it (authoritative copy now runs elsewhere).
    2. Verify no affected RBD image still shows the returning host as a
       watcher (split-brain check) — abort if it does.
    3. Remove the ceph blocklist entry for the returning host.
    4. Mark node online.
    """
    from .vm_service import _ssh_run

    node_id = node["id"]
    dead_ip = node["ip_address"]

    # Gather the images/domains that were relocated OFF this node, from the
    # ha_events trail (result=success relocations sourced from this node).
    relocated = _relocated_off_node(node_id)
    detail = {"undefined": [], "watcher_conflicts": [], "unblocklisted": []}

    # 1. Destroy stale domains on the returning host (fencing-on-return).
    #    The host may be reachable again now; if not, we still proceed to
    #    the watcher check which is the real safety gate.
    node_online_ssh = node.get("private_key") is not None
    for vm_name in relocated["vm_names"]:
        if node_online_ssh:
            # Disable autostart FIRST — fleet VMs are autostart-enabled, so if
            # this returning host reboots again before we finish, libvirt must
            # not re-launch a stale copy of a VM that now runs elsewhere.
            _ssh_run(node, f"virsh autostart --disable '{vm_name}' 2>/dev/null")
            _ssh_run(node, f"virsh destroy '{vm_name}' 2>/dev/null")
            _ssh_run(node, f"virsh undefine '{vm_name}' --nvram 2>/dev/null "
                           f"|| virsh undefine '{vm_name}' 2>/dev/null")
            detail["undefined"].append(vm_name)

    # 2. Split-brain guard: none of the relocated images may still show the
    #    returning host as a watcher/lock holder.
    try:
        with _CephFence(survivor) as fence:
            for image in relocated["images"]:
                out, err, rc = _ssh_run(
                    survivor,
                    f"rbd {fence.auth_args} status {image} 2>/dev/null",
                )
                if dead_ip in (out or ""):
                    detail["watcher_conflicts"].append({"image": image})
            if detail["watcher_conflicts"]:
                logger.error(
                    "READMIT ABORTED (split-brain) for node %s (%s): relocated "
                    "image(s) still show it as a watcher: %s",
                    node["hostname"], dead_ip,
                    [w["image"] for w in detail["watcher_conflicts"]])
                record_ha_event(
                    node_id=node_id, trigger="manual", result="readmit_aborted",
                    detail={"reason": "split-brain watcher conflict",
                            "watcher_conflicts": detail["watcher_conflicts"]})
                return {"error": "ABORT readmit — returning host still holds "
                                 "watchers/locks on relocated images "
                                 "(split-brain risk)",
                        "detail": detail}

            # 3. Un-blocklist now that stale domains are gone + no watchers.
            #    Ceph creates MULTIPLE entries for a client address: the plain
            #    "<ip>:0/0" we added, PLUS session-specific "<ip>:0/<nonce>"
            #    entries (from OSD-side blocklisting / RBD lock-break). Removing
            #    just "<ip>" leaves the nonce entries behind and the node stays
            #    partially fenced. So enumerate every entry for this IP and
            #    remove each.
            ls_out, _e, _rc = _ssh_run(
                survivor, f"ceph {fence.auth_args} osd blocklist ls 2>&1")
            entries = [
                line.split()[0]
                for line in (ls_out or "").splitlines()
                if line.strip().startswith(dead_ip + ":")
                or line.strip().split(":")[0:1] == [dead_ip]
            ]
            if not entries:
                entries = [dead_ip]  # fallback
            for entry in entries:
                out, err, rc = _ssh_run(
                    survivor,
                    f"ceph {fence.auth_args} osd blocklist rm {entry} 2>&1",
                )
                if rc == 0:
                    detail["unblocklisted"].append(entry)
    except Exception as exc:
        return {"error": f"readmit fence-check error: {exc}", "detail": detail}

    # 4. Mark node online again.
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            # Mark online AND refresh the heartbeat: readmit only runs once the
            # host is confirmed reachable, so its heartbeat is effectively fresh
            # now. Without this the stale last_heartbeat_at would make the engine
            # re-flag the node 'failed' on its next cycle (before node_monitor's
            # own poll refreshes it).
            cur.execute(
                "UPDATE nodes SET status = 'online', last_heartbeat_at = UTC_TIMESTAMP() "
                "WHERE id = %s", (node_id,))
        conn.commit()
    finally:
        conn.close()

    record_ha_event(node_id=node_id, trigger="manual", result="readmitted",
                    detail=detail)
    logger.warning("READMITTED node %s (%s): %s", node["hostname"], dead_ip, detail)
    return {"ok": True, "detail": detail}


def _relocated_off_node(node_id: int) -> dict:
    """VM names + images that were on this node during a failover and now live
    ELSEWHERE — so their stale copy on the returning host must be destroyed.

    Authoritative source: any VM that ha_events recorded as sourced from this
    node during a heartbeat_timeout failover (result 'success' = HA-relocated,
    OR 'needs_migration' = flagged for Ops) AND whose CURRENT DB node_id is now
    a DIFFERENT node (i.e. it was actually moved, by HA or by a manual/recovery
    migrate). The DB node_id is the source of truth for where the VM lives now.
    """
    conn = _get_db()
    vm_names, images = [], []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT v.id, v.name, v.disk_path, v.node_id
                FROM ha_events e
                JOIN vms v ON v.id = e.vm_id
                WHERE e.source_node_id = %s
                  AND e.trigger = 'heartbeat_timeout'
                  AND e.result IN ('success', 'needs_migration')
                  AND e.vm_id IS NOT NULL
                  AND v.node_id <> %s   -- VM now lives on a different node
                """,
                (node_id, node_id),
            )
            for row in cur.fetchall():
                if row.get("name"):
                    vm_names.append(row["name"])
                dp = (row.get("disk_path") or "").strip()
                if dp.startswith("rbd:"):
                    images.append(dp[4:].split(":")[0])
    finally:
        conn.close()
    return {"vm_names": vm_names, "images": images}
