"""VM lifecycle — event ledger + 1-year TTL helpers.

Two responsibilities:

1. Append-only event ledger (create/delete/renew/warn/auto_stop/auto_delete/
   exempt) that powers the cumulative VM-count chart and an audit trail.
   Best-effort: failures are logged and swallowed — never break a VM operation
   for a metrics/audit side-effect.

2. TTL helpers used by the API (renew, exempt) and the daily expiry checker
   (app.tasks.vm_expiry): compute a new expiry, renew a term, toggle exempt.

The term length and all warn/stop/delete offsets are configured in
app.config.Config (LIFECYCLE_* env vars) so a test environment can run the whole
machine in minutes.
"""

import logging
import os
from datetime import datetime, timedelta
from urllib.parse import urlparse

import pymysql
import pymysql.cursors

logger = logging.getLogger("wvc.vm-lifecycle")

# Recognised ledger events. 'create'/'delete' predate the TTL work and feed the
# metrics chart; the rest are the TTL audit trail.
EVENTS = {
    "create", "delete", "renew", "warn",
    "auto_stop", "auto_delete", "exempt", "unexempt",
}


def _db():
    url = urlparse(os.environ.get("DATABASE_URL", ""))
    return pymysql.connect(
        host=url.hostname, port=url.port or 3306, user=url.username,
        password=url.password, database=url.path.lstrip("/"),
        cursorclass=pymysql.cursors.DictCursor,
    )


def record(event: str, *, os_family: str | None = None,
           vm_name: str | None = None, vm_id: int | None = None) -> None:
    """Append a lifecycle event (best-effort)."""
    # Defensive cap: the event column is VARCHAR(32) (mig 0015); never let an
    # over-length value cause a strict-mode INSERT rejection.
    event = (event or "")[:32]
    try:
        conn = _db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO vm_lifecycle_events (ts, event, os_family, vm_name, vm_id) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (datetime.utcnow(), event, os_family or "unknown", vm_name, vm_id),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.warning("vm_lifecycle record(%s) failed", event, exc_info=True)


# --------------------------------------------------------------------------
# TTL helpers
# --------------------------------------------------------------------------

def _ttl_days() -> float:
    """Term length in days, from config (fallback 365)."""
    try:
        from flask import current_app
        return float(current_app.config.get("LIFECYCLE_TTL_DAYS", 365))
    except Exception:
        return float(os.environ.get("LIFECYCLE_TTL_DAYS", "365"))


def new_expiry(now: datetime | None = None) -> datetime:
    """Compute an expiry = now + TTL. Used at create and on renew."""
    now = now or datetime.utcnow()
    return now + timedelta(days=_ttl_days())


def renew(vm_id: int) -> dict:
    """Reset a VM's term: expires_at = now + TTL, renewed_at = now,
    lifecycle_state back to 'active'. Does NOT clear 'exempt'.

    Returns {"ok": True, "expires_at": iso, "vm_name": name} or {"error": ...}.
    The caller is responsible for restarting an auto_stopped VM if desired.
    """
    now = datetime.utcnow()
    exp = new_expiry(now)
    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, os_family, lifecycle_state FROM vms WHERE id = %s",
                        (vm_id,))
            row = cur.fetchone()
            if not row:
                return {"error": "VM not found"}
            cur.execute(
                "UPDATE vms SET expires_at = %s, renewed_at = %s, "
                "lifecycle_state = 'active' WHERE id = %s",
                (exp, now, vm_id),
            )
        conn.commit()
    finally:
        conn.close()
    record("renew", os_family=row.get("os_family"), vm_name=row.get("name"), vm_id=vm_id)
    logger.info("VM %s (id=%d) renewed until %s", row.get("name"), vm_id, exp.isoformat())
    return {"ok": True, "expires_at": exp.isoformat(),
            "vm_name": row.get("name"),
            "was_state": row.get("lifecycle_state")}


def set_exempt(vm_id: int, exempt: bool) -> dict:
    """Admin-only: toggle a VM's TTL exemption. Exempt VMs are never warned,
    stopped or deleted by the expiry checker."""
    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, os_family FROM vms WHERE id = %s", (vm_id,))
            row = cur.fetchone()
            if not row:
                return {"error": "VM not found"}
            cur.execute("UPDATE vms SET exempt = %s WHERE id = %s",
                        (1 if exempt else 0, vm_id))
        conn.commit()
    finally:
        conn.close()
    record("exempt" if exempt else "unexempt",
           os_family=row.get("os_family"), vm_name=row.get("name"), vm_id=vm_id)
    logger.info("VM %s (id=%d) exempt=%s", row.get("name"), vm_id, exempt)
    return {"ok": True, "exempt": bool(exempt), "vm_name": row.get("name")}


# --------------------------------------------------------------------------
# Runtime enable switch (UI-toggleable, no pod restart)
# --------------------------------------------------------------------------
# The env var LIFECYCLE_ENABLED is only a startup DEFAULT. The authoritative
# LIVE switch is a row in the generic `ha_settings` key/value table
# (name='lifecycle_enabled'), flipped from the admin UI. The engine reads
# is_lifecycle_enabled() at the top of every run, so ops can arm/disarm the
# 1-year-TTL checker without a redeploy — mirroring the HA runtime kill-switch.
#
# Resolution: if a runtime row exists it wins; otherwise fall back to the
# LIFECYCLE_ENABLED env default. This lets a fresh deploy inherit its env
# posture until an admin explicitly toggles it.

_LIFECYCLE_SETTING = "lifecycle_enabled"


def is_lifecycle_enabled(env_default: bool = False) -> bool:
    """Authoritative runtime switch for the lifecycle checker.

    Returns the ha_settings row value if set, else the env default. Fail-safe:
    any read error returns the env default.
    """
    try:
        from .ha_settings_service import get_setting
        val = get_setting(_LIFECYCLE_SETTING, None)
    except Exception:
        val = None
    if val is None:
        return env_default
    return str(val).lower() == "true"


def set_lifecycle_enabled(enabled: bool, updated_by: str | None = None) -> None:
    """Flip the runtime lifecycle switch (admin UI)."""
    from .ha_settings_service import set_setting
    set_setting(_LIFECYCLE_SETTING, "true" if enabled else "false",
                updated_by=updated_by)
    logger.info("lifecycle_enabled runtime set to %s by %s", enabled, updated_by)


def lifecycle_status(env_default: bool = False) -> dict:
    """Return {enabled, source} for the UI toggle."""
    try:
        from .ha_settings_service import get_setting
        val = get_setting(_LIFECYCLE_SETTING, None)
    except Exception:
        val = None
    if val is None:
        return {"enabled": env_default, "source": "env_default"}
    return {"enabled": str(val).lower() == "true", "source": "runtime"}
