"""Configuration from environment variables (12-factor)."""

import os


class Config:
    """Base configuration."""

    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "change-me-in-production")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "mysql+pymysql://root:root@localhost:3306/kvmaestro"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # JWT
    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "change-me-in-production")
    JWT_ACCESS_TOKEN_EXPIRES = int(os.environ.get("JWT_ACCESS_TOKEN_EXPIRES", 900))
    JWT_REFRESH_TOKEN_EXPIRES = int(
        os.environ.get("JWT_REFRESH_TOKEN_EXPIRES", 604800)
    )

    # Redis / Celery
    REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    CELERY_BROKER_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    CELERY_RESULT_BACKEND = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    # -----------------------------------------------------------------
    # Bootstrap admin (open-source first-boot / break-glass account)
    # -----------------------------------------------------------------
    # A built-in LOCAL admin that bypasses LDAP entirely. Username is fixed
    # ("admin" by default); the password is ADMIN_PASSWORD. When ADMIN_PASSWORD
    # is empty the account is DISABLED. Purpose: administer a fresh deployment
    # before any LDAP directory is configured (LDAP is set up from the Users
    # tab), and provide a break-glass login if LDAP is later misconfigured or
    # unreachable. Always is_admin=1. Set a strong ADMIN_PASSWORD via secret.
    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

    # LDAP — NOTE (open-sourcing, migration 0016): these env vars are now only a
    # one-time SEED. On first migrate they are copied into the app_settings DB
    # table, after which the DB rows (editable in the admin Users tab, no
    # redeploy) are AUTHORITATIVE and these env values are ignored at runtime.
    # Kept here so an existing env-configured deployment seeds cleanly and so
    # a fresh install has sane blank defaults.
    LDAP_ENABLED = os.environ.get("LDAP_ENABLED", "false").lower() == "true"
    LDAP_URI = os.environ.get("LDAP_URI", "ldaps://ldap.example.com:636")
    LDAP_BASE_DN = os.environ.get("LDAP_BASE_DN", "dc=example,dc=com")
    LDAP_BIND_DN = os.environ.get("LDAP_BIND_DN", "")
    LDAP_BIND_PASSWORD = os.environ.get("LDAP_BIND_PASSWORD", "")
    LDAP_USER_FILTER = os.environ.get("LDAP_USER_FILTER", "(uid={username})")
    LDAP_GROUP_BASE_DN = os.environ.get(
        "LDAP_GROUP_BASE_DN", "ou=groups,dc=example,dc=com"
    )
    LDAP_ADMIN_GROUP = os.environ.get("LDAP_ADMIN_GROUP", "kvm-admins")

    # Agent
    AGENT_SHARED_KEY = os.environ.get("AGENT_SHARED_KEY", "change-me-in-production")

    # Environment identity — "live" (default) or "test" (a test environment).
    # Surfaced to the UI (red border + "test" theme).
    ENVIRONMENT = os.environ.get("ENVIRONMENT", "live").lower()

    # HA thresholds
    HEARTBEAT_INTERVAL = int(os.environ.get("HEARTBEAT_INTERVAL", 10))
    NODE_SUSPECT_THRESHOLD = int(os.environ.get("NODE_SUSPECT_THRESHOLD", 30))
    NODE_DOWN_THRESHOLD = int(os.environ.get("NODE_DOWN_THRESHOLD", 60))

    # -----------------------------------------------------------------
    # VM-HA (automatic failover of VMs off a dead KVM host)
    # -----------------------------------------------------------------
    # Master switch default. When false (the default) the HA engine runs
    # in DETECT-ONLY mode: it identifies stale nodes and writes ha_events
    # rows, but NEVER fences a node or relocates a VM. Set to "true" only
    # on a cluster where dedicated HA hosts share Ceph and you have
    # verified the fencing path. Flipping this on is what arms
    # fence-then-restart.
    #
    # NOTE: the authoritative runtime switch is the ha_settings DB row
    # (admin UI toggle); this env var only seeds it on first migration.
    HA_ENABLED = os.environ.get("HA_ENABLED", "false").lower() == "true"

    # Seconds since a node's last_heartbeat_at before it is considered
    # DOWN and eligible for failover (must exceed the node_monitor poll
    # interval by a comfortable margin to avoid false positives).
    HA_FAILOVER_THRESHOLD = int(os.environ.get("HA_FAILOVER_THRESHOLD", 120))

    # When fencing, also break the RBD exclusive-lock on each affected
    # image (in addition to ceph osd blocklist). Ceph blocklist alone
    # expires the dead client's lock eventually, but breaking it makes
    # the surviving host able to write immediately. Safe because the
    # dead host is already blocklisted first.
    HA_BREAK_RBD_LOCK = os.environ.get("HA_BREAK_RBD_LOCK", "true").lower() == "true"

    # Ceph credentials used ONLY for HA fencing (osd blocklist + rbd
    # lock-break). This is a dedicated least-privilege Ceph user
    # (client.wvc-ha: mon 'allow r, allow command "osd blocklist"',
    # osd 'profile rbd pool=libvirt-pool') — NOT client.libvirt, which
    # lacks blocklist caps. The key is held here (single source of truth,
    # rotate in one place); at fence time ha_service writes a temp keyring
    # on a SURVIVING node over SSH, runs the fenced command, then removes
    # it — no persistent privileged keyring sits on the KVM fleet.
    HA_CEPH_USER = os.environ.get("HA_CEPH_USER", "wvc-ha")
    HA_CEPH_KEY = os.environ.get("HA_CEPH_KEY", "")
    # Ceph mon hosts for the fencing commands (comma-separated). Falls
    # back to the mons already present in each node's rbd disk template
    # when unset.
    HA_CEPH_MON_HOST = os.environ.get("HA_CEPH_MON_HOST", "")

    # HA notifications (Webex). When both are set, the engine posts a card on
    # every failover (fence/relocate outcome). ENVIRONMENT prefixes test cards.
    WEBEX_BOT_TOKEN = os.environ.get("WEBEX_BOT_TOKEN", "")
    WEBEX_HA_ROOM_ID = os.environ.get("WEBEX_HA_ROOM_ID", "")
    WEBEX_DEEP_LINK_BASE = os.environ.get(
        "WEBEX_DEEP_LINK_BASE", "https://kvmaestro.example.com")

    # DNS domain suffix appended to guest hostnames to form their FQDN in
    # cloud-init (e.g. "<vmname>.<VM_DOMAIN_SUFFIX>"). Leave blank to emit no
    # domain suffix (hostname only).
    VM_DOMAIN_SUFFIX = os.environ.get("VM_DOMAIN_SUFFIX", "example.local")

    # Email domain used to derive a user's address from their username when the
    # users.email column is empty (address = "<username>@<EMAIL_DOMAIN>"). Used
    # for VM-lifecycle owner notifications. Set to your org's mail domain.
    EMAIL_DOMAIN = os.environ.get("EMAIL_DOMAIN", "example.com")


    # -----------------------------------------------------------------
    # VM lifecycle / 1-year TTL (migrated from the self-service portal)
    # -----------------------------------------------------------------
    # Master switch for the daily expiry checker (Celery beat task
    # app.tasks.vm_expiry). When false, no VM is ever auto-stopped or
    # auto-deleted and no expiry warnings are sent — the columns still
    # exist and renew/exempt still work, but enforcement is off. Ships
    # OFF; arm deliberately per environment.
    LIFECYCLE_ENABLED = os.environ.get("LIFECYCLE_ENABLED", "false").lower() == "true"

    # The term length granted at create and on each renew. Real value is
    # 365 days; a test environment can shrink this (and the offsets below) to
    # minutes to exercise the whole machine quickly. All periods are DAYS and
    # accept fractions (e.g. "0.01" ≈ 15 min).
    LIFECYCLE_TTL_DAYS = float(os.environ.get("LIFECYCLE_TTL_DAYS", "365"))

    # Pre-expiry warnings, expressed as DAYS BEFORE expires_at.
    #   11 months   -> ~30 days before   (365 - 335)
    #   11.5 months -> ~15 days before   (365 - 350)
    LIFECYCLE_WARN1_DAYS_BEFORE = float(
        os.environ.get("LIFECYCLE_WARN1_DAYS_BEFORE", "30"))
    LIFECYCLE_WARN2_DAYS_BEFORE = float(
        os.environ.get("LIFECYCLE_WARN2_DAYS_BEFORE", "15"))

    # Post-expiry timeline (DAYS AFTER expires_at). The flow is:
    #   12 mo   -> auto-STOP + warn owner; VM cannot be restarted except by
    #              renewing (STOP grace, default 0 = at expiry).
    #   12.5 mo -> repeat the stop warning to the owner (~15 days after).
    #   13 mo   -> post an Ops-room delete REQUEST (admin deep-link) — the VM
    #              is NOT auto-deleted; deletion is a human/admin action.
    LIFECYCLE_STOP_GRACE_DAYS = float(
        os.environ.get("LIFECYCLE_STOP_GRACE_DAYS", "0"))
    LIFECYCLE_POSTSTOP_WARN_DAYS = float(
        os.environ.get("LIFECYCLE_POSTSTOP_WARN_DAYS", "15"))
    # Days after expiry to notify Ops that the VM should be deleted (no
    # automatic deletion ever happens).
    LIFECYCLE_OPS_DELETE_DAYS = float(
        os.environ.get("LIFECYCLE_OPS_DELETE_DAYS", "30"))

    # HARD SAFETY: when false, the lifecycle engine NEVER performs a
    # destructive hypervisor action (virsh stop / undefine / rbd rm). It still
    # sends warnings, records events and advances lifecycle_state so the whole
    # machine is observable — but no VM is ever actually stopped or deleted.
    # MUST be false in a test environment (a throwaway test control plane that
    # manages REAL shared lab VMs — an auto-delete there would destroy a
    # production VM). Defaults true so real environments enforce normally.
    LIFECYCLE_DESTRUCTIVE = os.environ.get(
        "LIFECYCLE_DESTRUCTIVE", "true").lower() == "true"

    # Owner-facing deep link + Ops room reuse the HA Webex config
    # (WEBEX_BOT_TOKEN + WEBEX_HA_ROOM_ID). Owners are DM'd by
    # {username}@<EMAIL_DOMAIN>; the Ops room gets stop/delete summaries.

class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
}
