"""Admin-only utility endpoints (recent app logs viewer, LDAP config, etc.)."""

import logging

from flask import Blueprint, jsonify, request

from ..middleware.auth import admin_required
from ..middleware.mcp import current_user_id
from ..services import log_buffer, settings_service, ldap_config_service

logger = logging.getLogger("wvc.api.admin")

bp = Blueprint("admin", __name__)


# Maps the JSON field name <-> app_settings key. `secret` fields are never
# returned to the client; the client only learns whether one is set.
_LDAP_FIELDS = {
    "enabled":       ("ldap_enabled",       False),
    "uri":           ("ldap_uri",           False),
    "base_dn":       ("ldap_base_dn",       False),
    "bind_dn":       ("ldap_bind_dn",       False),
    "bind_password": ("ldap_bind_password", True),
    "user_filter":   ("ldap_user_filter",   False),
    "group_base_dn": ("ldap_group_base_dn", False),
    "admin_group":   ("ldap_admin_group",   False),
}


def _current_username() -> str | None:
    """Best-effort username for the audit column (updated_by)."""
    import os
    import pymysql
    import pymysql.cursors
    from urllib.parse import urlparse

    try:
        uid = current_user_id()
        url = urlparse(os.environ.get("DATABASE_URL", ""))
        conn = pymysql.connect(
            host=url.hostname, port=url.port or 3306, user=url.username,
            password=url.password, database=url.path.lstrip("/"),
            cursorclass=pymysql.cursors.DictCursor,
        )
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT username FROM users WHERE id = %s", (uid,))
                row = cur.fetchone()
                return row["username"] if row else None
        finally:
            conn.close()
    except Exception:
        return None


@bp.route("/ldap-config", methods=["GET"])
@admin_required
def get_ldap_config():
    """Return the effective LDAP config for the Users tab form.

    The bind password is NEVER returned. Instead ``bind_password_set`` tells the
    UI whether one is configured (so it can show "configured — leave blank to
    keep" vs an empty field). All other fields reflect the effective value
    (DB row if set, else the env seed).
    """
    cfg = ldap_config_service.get_effective()
    return jsonify({
        "enabled":            cfg["enabled"],
        "uri":                cfg["uri"],
        "base_dn":            cfg["base_dn"],
        "bind_dn":            cfg["bind_dn"],
        "bind_password_set":  settings_service.is_secret_set("ldap_bind_password"),
        "user_filter":        cfg["user_filter"],
        "group_base_dn":      cfg["group_base_dn"],
        "admin_group":        cfg["admin_group"],
    }), 200


@bp.route("/ldap-config", methods=["PUT"])
@admin_required
def update_ldap_config():
    """Persist LDAP config changes to app_settings (no redeploy).

    Body accepts any subset of the config fields. The bind password is
    write-only: send a non-empty ``bind_password`` to set it, omit it (or send
    null) to leave the stored one untouched, or send an explicit empty string
    with ``clear_bind_password: true`` to remove it.
    """
    data = request.get_json(silent=True) or {}
    updated_by = _current_username()
    changed = []

    for field, (setting_name, is_secret) in _LDAP_FIELDS.items():
        if is_secret:
            continue  # handled below
        if field not in data:
            continue
        value = data[field]
        if field == "enabled":
            value = "true" if bool(value) else "false"
        settings_service.set_setting(
            setting_name, "" if value is None else str(value),
            updated_by=updated_by, is_secret=False,
        )
        changed.append(field)

    # Bind password: explicit clear, or set-if-provided, else leave as-is.
    if data.get("clear_bind_password"):
        settings_service.set_setting(
            "ldap_bind_password", "", updated_by=updated_by, is_secret=True
        )
        changed.append("bind_password(cleared)")
    else:
        bp_val = data.get("bind_password")
        if bp_val:
            settings_service.set_setting(
                "ldap_bind_password", str(bp_val),
                updated_by=updated_by, is_secret=True,
            )
            changed.append("bind_password")

    logger.info("LDAP config updated by %s: %s", updated_by, ", ".join(changed) or "(no changes)")
    return jsonify({"status": "ok", "changed": changed}), 200


@bp.route("/ldap-config/test", methods=["POST"])
@admin_required
def test_ldap_config():
    """Test the CURRENTLY SAVED LDAP config by binding as the service account.

    Does not accept credentials in the body — it validates what is persisted, so
    an admin can confirm a save actually works. Returns whether the service bind
    succeeded and how many entries the base DN search sees.
    """
    from ldap3 import Connection, SUBTREE
    from ..services.ldap_service import _build_server

    cfg = ldap_config_service.get_effective()
    if not (cfg["uri"] and cfg["bind_dn"] and cfg["base_dn"]):
        return jsonify({"ok": False, "error": "LDAP is not fully configured"}), 400
    try:
        server = _build_server(cfg["uri"])
        conn = Connection(
            server, user=cfg["bind_dn"], password=cfg["bind_password"],
            auto_bind=True, auto_referrals=False,
        )
        conn.search(cfg["base_dn"], "(objectClass=*)", search_scope=SUBTREE,
                    size_limit=1)
        return jsonify({"ok": True, "message": "Service bind succeeded"}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200


@bp.route("/logs", methods=["GET"])
@admin_required
def logs():
    """Recent application logs from this process's in-memory ring buffer.

    Query params:
      limit    — max records (default 500, cap 2000)
      level    — minimum level: DEBUG|INFO|WARNING|ERROR|CRITICAL
      contains — case-insensitive substring filter on message/logger

    Note: per-process buffer (the API pod handling this request). Convenience
    view of recent activity, not a durable/complete log store.
    """
    try:
        limit = int(request.args.get("limit", "500"))
    except ValueError:
        limit = 500
    limit = max(1, min(limit, 2000))
    level = request.args.get("level") or None
    contains = request.args.get("contains") or None
    records = log_buffer.get_records(limit=limit, level=level, contains=contains)
    return jsonify({"logs": records, "count": len(records)}), 200
