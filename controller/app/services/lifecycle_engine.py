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

"""VM lifecycle expiry engine — the daily grace machine.

Runs (via the Celery beat task app.tasks.vm_expiry) once a day. For every
non-exempt VM it looks at expires_at and the configured offsets and drives the
state machine:

  now < expires_at - WARN1 : nothing
  within WARN1 / WARN2 window : DM owner a warning (deduped once per stage/day)
  now >= expires_at + STOP_GRACE (default 0) and running : AUTO-STOP + DM + Ops
  now >= expires_at + POSTSTOP_WARN and still stopped : DM a final warning
  now >= expires_at + DELETE_GRACE and not renewed : AUTO-DELETE (real vm_delete)

Renewing a VM (expires_at pushed out, lifecycle_state='active') naturally drops
it back out of every window. Exempt VMs are skipped entirely.

Dedup: we don't want to DM the same warning every run. We record a 'warn' event
in vm_lifecycle_events and check whether we've already warned for the current
stage since the VM's current term began (renewed_at/created_at). auto_stop and
auto_delete are naturally idempotent via lifecycle_state / row removal.

Enforcement is gated by LIFECYCLE_ENABLED — off by default.
"""

import logging
from datetime import datetime, timedelta

from flask import current_app

from .vm_lifecycle import _db, record
from . import notify_vm_lifecycle as notify

logger = logging.getLogger("wvc.lifecycle-engine")


def _cfg(key, default):
    return current_app.config.get(key, default)


def _already_warned(cur, vm_id: int, stage: str, term_start: datetime) -> bool:
    """Has a 'warn' event for this stage been recorded since the current term
    began? We encode the stage in os_family-free way by storing it in vm_name?
    No — we add a dedicated marker: we look at ts >= term_start and count warns.
    To distinguish stages we store the stage in the event string suffix."""
    cur.execute(
        "SELECT COUNT(*) AS n FROM vm_lifecycle_events "
        "WHERE vm_id = %s AND event = %s AND ts >= %s",
        (vm_id, f"warn:{stage}", term_start),
    )
    return (cur.fetchone() or {}).get("n", 0) > 0


def _mark_warned(vm_id: int, stage: str, vm_name: str, os_family):
    # store stage-qualified event so dedup can distinguish pre1/pre2/poststop
    record(f"warn:{stage}", os_family=os_family, vm_name=vm_name, vm_id=vm_id)


def run_expiry_check(dry_run: bool = False) -> dict:
    """Main entry. Returns a stats dict; logs decisions."""
    # Authoritative switch: runtime ha_settings row (UI toggle) wins; falls
    # back to the LIFECYCLE_ENABLED env var as the startup default.
    from .vm_lifecycle import is_lifecycle_enabled
    env_default = bool(_cfg("LIFECYCLE_ENABLED", False))
    enabled = is_lifecycle_enabled(env_default)
    ttl = float(_cfg("LIFECYCLE_TTL_DAYS", 365))
    warn1 = float(_cfg("LIFECYCLE_WARN1_DAYS_BEFORE", 30))
    warn2 = float(_cfg("LIFECYCLE_WARN2_DAYS_BEFORE", 15))
    stop_grace = float(_cfg("LIFECYCLE_STOP_GRACE_DAYS", 0))
    poststop_warn = float(_cfg("LIFECYCLE_POSTSTOP_WARN_DAYS", 15))
    ops_delete = float(_cfg("LIFECYCLE_OPS_DELETE_DAYS", 30))
    # HARD safety: in a test control plane this is false and no VM is ever
    # actually stopped — the engine only simulates + notifies. (Deletion is
    # never automatic in any environment — it is always an admin action.)
    destructive = bool(_cfg("LIFECYCLE_DESTRUCTIVE", True))

    now = datetime.utcnow()
    stats = {"checked": 0, "warned": 0, "stopped": 0,
             "simulated_stop": 0, "ops_delete_requested": 0,
             "skipped_exempt": 0, "enrolled": 0, "enabled": enabled,
             "dry_run": dry_run, "destructive": destructive}

    # ----------------------------------------------------------------------
    # Continuous enrollment. Give every non-exempt VM that lacks a term one
    # (expires_at = created_at + TTL), so a VM never sits in "limbo" (no
    # expiry AND not exempt). Migration 0014 did this ONCE at install; but
    # vm_sync inserts newly-DISCOVERED VMs with expires_at=NULL, so without a
    # continuous backfill those VMs escape lifecycle forever. Exempt VMs are
    # intentionally left NULL (no term). Runs regardless of the enforcement
    # switch — enrollment is metadata, never an action. (INTERVAL in SECONDS
    # so fractional TTLs used by test control planes work too.)
    ttl_seconds = int(ttl * 86400)
    _enr_conn = _db()
    try:
        with _enr_conn.cursor() as _enr_cur:
            _enr_cur.execute(
                "UPDATE vms SET expires_at = created_at + INTERVAL %s SECOND, "
                "renewed_at = COALESCE(renewed_at, created_at) "
                "WHERE expires_at IS NULL AND exempt = 0",
                (ttl_seconds,),
            )
            stats["enrolled"] = _enr_cur.rowcount
        _enr_conn.commit()
        if stats["enrolled"]:
            logger.info("Lifecycle: enrolled %d un-termed VM(s) "
                        "(expires_at = created_at + %.4gd)", stats["enrolled"], ttl)
    finally:
        _enr_conn.close()

    if not enabled:
        logger.info("Lifecycle enforcement disabled (LIFECYCLE_ENABLED=false) — no-op")
        return stats

    # Resolve owner username lazily per VM.
    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT v.id, v.name, v.state, v.os_family, v.expires_at, "
                "       v.renewed_at, v.created_at, v.exempt, v.lifecycle_state, "
                "       v.owner_user_id, v.tenant_id, u.username AS owner_username "
                "FROM vms v LEFT JOIN users u ON u.id = v.owner_user_id "
                "WHERE v.expires_at IS NOT NULL"
            )
            vms = cur.fetchall() or []
    finally:
        conn.close()

    for vm in vms:
        stats["checked"] += 1
        if vm.get("exempt"):
            stats["skipped_exempt"] += 1
            continue

        vm_id = vm["id"]
        name = vm["name"]
        osf = vm.get("os_family")
        owner = vm.get("owner_username")
        tenant_id = vm.get("tenant_id")
        exp = vm["expires_at"]
        lc = vm.get("lifecycle_state")
        term_start = vm.get("renewed_at") or vm.get("created_at") or (exp - timedelta(days=ttl))

        stop_at = exp + timedelta(days=stop_grace)
        poststop_warn_at = exp + timedelta(days=poststop_warn)
        ops_delete_at = exp + timedelta(days=ops_delete)
        warn1_at = exp - timedelta(days=warn1)
        warn2_at = exp - timedelta(days=warn2)

        # A VM is "stopped by expiry" once we've marked it auto_stopped (or
        # escalated to pending_delete). Both are post-expiry states.
        expired_stopped = lc in ("auto_stopped", "pending_delete")

        # ---- 13 mo: Ops DELETE REQUEST (no automatic deletion) -----------
        # Post an Ops-room card asking an admin to delete the VM, with a
        # deep-link. Fires once (dedup via lifecycle_state -> pending_delete).
        if now >= ops_delete_at and expired_stopped:
            if lc != "pending_delete":
                logger.info("lifecycle: OPS-DELETE-REQUEST %s (id=%d) exp=%s%s",
                            name, vm_id, exp, " [dry-run]" if dry_run else "")
                if not dry_run:
                    _set_state(vm_id, "pending_delete")
                    record("ops_delete_req", os_family=osf, vm_name=name, vm_id=vm_id)
                    notify.ops_delete_request(name, owner, vm_id)
                stats["ops_delete_requested"] += 1
            continue

        # ---- AUTO-STOP at expiry (12 mo) ---------------------------------
        running = vm.get("state") not in ("stopped", "crashed", None)
        if now >= stop_at and not expired_stopped:
            logger.info("lifecycle: AUTO-STOP %s (id=%d) exp=%s running=%s%s%s",
                        name, vm_id, exp, running,
                        " [dry-run]" if dry_run else "",
                        " [SIMULATED — non-destructive]" if not destructive else "")
            if not dry_run:
                # Only issue a real virsh stop if it's actually running AND this
                # environment is allowed to perform destructive actions. In a
                # test control plane (destructive=false) we NEVER touch the
                # hypervisor — we just mark the state and notify.
                if running and destructive:
                    from .vm_service import vm_action
                    vm_action(vm_id, "stop")
                _set_state(vm_id, "auto_stopped")
                record("auto_stop", os_family=osf, vm_name=name, vm_id=vm_id)
                notify.warn_owner(name, owner, stage="stopped",
                                  days_left=0, expires_at=exp, tenant_id=tenant_id, vm_id=vm_id)
                notify.ops_summary("auto_stop", name, owner)
                if not destructive:
                    stats["simulated_stop"] += 1
            stats["stopped"] += 1
            # The VM stays stopped and cannot be restarted except by renewing
            # (enforced in vm_service.vm_action). poststop-warn / ops-delete
            # are handled on subsequent runs.
            continue

        # ---- 12.5 mo: repeat the stop warning to the owner ---------------
        if now >= poststop_warn_at and expired_stopped:
            if not _warned(vm_id, "poststop", term_start):
                logger.info("lifecycle: POSTSTOP-WARN %s (id=%d)%s",
                            name, vm_id, " [dry-run]" if dry_run else "")
                if not dry_run:
                    notify.warn_owner(name, owner, stage="poststop",
                                      days_left=(ops_delete_at - now).days,
                                      expires_at=exp, tenant_id=tenant_id, vm_id=vm_id)
                    _mark_warned(vm_id, "poststop", name, osf)
                stats["warned"] += 1
            continue

        # ---- PRE-EXPIRY warnings (WARN2 then WARN1) ----------------------
        if now >= warn2_at:
            if not _warned(vm_id, "pre2", term_start):
                if not dry_run:
                    notify.warn_owner(name, owner, stage="pre2",
                                      days_left=(exp - now).total_seconds() / 86400,
                                      expires_at=exp, tenant_id=tenant_id, vm_id=vm_id)
                    _mark_warned(vm_id, "pre2", name, osf)
                    _set_state(vm_id, "expiring")
                stats["warned"] += 1
            continue
        if now >= warn1_at:
            if not _warned(vm_id, "pre1", term_start):
                if not dry_run:
                    notify.warn_owner(name, owner, stage="pre1",
                                      days_left=(exp - now).total_seconds() / 86400,
                                      expires_at=exp, tenant_id=tenant_id, vm_id=vm_id)
                    _mark_warned(vm_id, "pre1", name, osf)
                    _set_state(vm_id, "expiring")
                stats["warned"] += 1
            continue

    logger.info("lifecycle check complete: %s", stats)
    return stats


def _set_state(vm_id: int, state: str):
    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE vms SET lifecycle_state = %s WHERE id = %s",
                        (state, vm_id))
        conn.commit()
    finally:
        conn.close()


def _warned(vm_id: int, stage: str, term_start: datetime) -> bool:
    conn = _db()
    try:
        with conn.cursor() as cur:
            return _already_warned(cur, vm_id, stage, term_start)
    finally:
        conn.close()
