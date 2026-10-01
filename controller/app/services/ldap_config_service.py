"""Effective LDAP configuration resolver.

Open-sourcing change: LDAP config moved from env-var-only into the DB
(``app_settings`` rows, migration 0016) so an admin can configure the directory
from the Users tab with no redeploy. Resolution order for every key:

    DB row (app_settings)  >  env var (Config)  >  hard default

The env var is now only a *seed/bootstrap* default — the migration copies it
into the DB on first run, after which the DB row is authoritative. Reading here
(rather than ``current_app.config``) is what lets an admin re-point the
directory at runtime.

Everything is fail-safe: if the DB/table is unavailable the env/config value is
used, so a database blip can never make the login path un-configurable.
"""

import logging

from flask import current_app

from . import settings_service

logger = logging.getLogger("wvc.ldap-config")

# (setting name in app_settings, Flask config key, is_secret)
_KEYS = {
    "enabled":        ("ldap_enabled",       "LDAP_ENABLED",       False),
    "uri":            ("ldap_uri",           "LDAP_URI",           False),
    "base_dn":        ("ldap_base_dn",       "LDAP_BASE_DN",       False),
    "bind_dn":        ("ldap_bind_dn",       "LDAP_BIND_DN",       False),
    "bind_password":  ("ldap_bind_password", "LDAP_BIND_PASSWORD", True),
    "user_filter":    ("ldap_user_filter",   "LDAP_USER_FILTER",   False),
    "group_base_dn":  ("ldap_group_base_dn", "LDAP_GROUP_BASE_DN", False),
    "admin_group":    ("ldap_admin_group",   "LDAP_ADMIN_GROUP",   False),
}


def _config_default(config_key: str) -> str:
    """Read the env-seeded Flask config value, normalising bool → string."""
    val = current_app.config.get(config_key, "")
    if isinstance(val, bool):
        return "true" if val else "false"
    return "" if val is None else str(val)


def _get(field: str) -> str:
    setting_name, config_key, is_secret = _KEYS[field]
    val = settings_service.get_setting(setting_name, default=None)
    if val is settings_service.DECRYPT_FAILED:
        # Sealed value unreadable (key rotated). Fall back to env so the
        # service account still binds; the admin should re-enter it in the UI.
        logger.warning(
            "LDAP %s could not be decrypted; falling back to env seed", field
        )
        return _config_default(config_key)
    if val is None or val == "":
        # Unset in DB → env/config seed.
        return _config_default(config_key)
    return val


def get_effective() -> dict:
    """Return the full effective LDAP config as a dict of strings/bool.

    ``enabled`` is returned as a bool; everything else as a string.
    ``bind_password`` is the real (decrypted) value — for internal use by the
    bind path only, never return this to the client.
    """
    cfg = {field: _get(field) for field in _KEYS}
    cfg["enabled"] = str(cfg["enabled"]).lower() == "true"
    return cfg


def is_configured() -> bool:
    """True when there is enough config to attempt an LDAP bind.

    Used by the auth path to decide whether to even try LDAP (so an
    unconfigured open-source install cleanly falls through to the bootstrap
    admin rather than throwing on an empty URI).
    """
    cfg = get_effective()
    return bool(cfg["enabled"] and cfg["uri"] and cfg["bind_dn"] and cfg["base_dn"])
