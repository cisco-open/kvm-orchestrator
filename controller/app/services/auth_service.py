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

"""Authentication service — bootstrap-admin + LDAP auth with raw SQL."""

import crypt
import hmac
import logging
import os
from datetime import datetime
from urllib.parse import urlparse

import pymysql
import pymysql.cursors
import redis as redis_lib
from flask import current_app
from flask_jwt_extended import create_access_token, create_refresh_token

logger = logging.getLogger("wvc.auth-service")


# v0.14.2 — Quick Create reads this back to populate cloud-init's
# `passwd:` field so the resulting Linux VM accepts the same CEC
# credentials the user just logged in with. Hash, never plaintext;
# TTL matches the JWT access-token TTL so the side-channel can't
# outlive the session.
#
# Note: stdlib `crypt` is deprecated in Python 3.11 and removed in
# 3.13. The controller image is pinned to python:3.11-slim so this is
# fine for now; when we bump to 3.13 swap to passlib's sha512_crypt.
_PASSWORD_HASH_KEY_FMT = "user_password_hash:{user_id}"


def _redis():
    return redis_lib.from_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
    )


def _stash_password_hash(user_id: int, password: str) -> None:
    """Compute a SHA-512 crypt(3) hash and SETEX it into Redis.

    Best-effort — a Redis outage must not prevent the user from
    logging in. We log and move on; the consequence is that the next
    Quick Create will fall back to asking the user for credentials.
    """
    try:
        ttl = int(current_app.config.get("JWT_ACCESS_TOKEN_EXPIRES", 900))
    except Exception:
        ttl = 900
    try:
        # `$6$<salt>$<hash>` — directly accepted by cloud-init's
        # `passwd:` field under `users:`.
        h = crypt.crypt(password, crypt.mksalt(crypt.METHOD_SHA512))
        r = _redis()
        r.setex(_PASSWORD_HASH_KEY_FMT.format(user_id=user_id), ttl, h)
    except Exception:
        logger.exception(
            "Failed to stash password hash for user_id=%s — Quick Create "
            "will require the user to supply credentials manually",
            user_id,
        )


def get_stashed_password_hash(user_id: int) -> str | None:
    """Return the SHA-512 crypt hash stashed at login, or None.

    None means: Redis miss (TTL expired, container restarted, or login
    never happened in this Redis lifetime). Callers must then either
    ask the user for credentials or refuse the operation.
    """
    try:
        r = _redis()
        return r.get(_PASSWORD_HASH_KEY_FMT.format(user_id=user_id))
    except Exception:
        logger.exception("Failed to read password hash for user_id=%s", user_id)
        return None


def hash_password(password: str) -> str:
    """Compute the same SHA-512 crypt hash used for the Redis stash.

    Exposed for the "use different credentials" branch of Quick Create:
    when the caller supplies an alternative password we hash it the
    same way before handing to cloud-init. Plaintext stops here.
    """
    return crypt.crypt(password, crypt.mksalt(crypt.METHOD_SHA512))


def _get_db():
    """Return a raw pymysql connection parsed from DATABASE_URL."""
    url = urlparse(os.environ.get("DATABASE_URL", ""))
    return pymysql.connect(
        host=url.hostname,
        port=url.port or 3306,
        user=url.username,
        password=url.password,
        database=url.path.lstrip("/"),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def authenticate(username: str, password: str) -> dict | None:
    """Authenticate a user. Returns user dict or None.

    Resolution order:
      1. Bootstrap admin — a built-in local account (username = ADMIN_USERNAME,
         default "admin") whose password is the ADMIN_PASSWORD env var. This
         account bypasses LDAP entirely and is always an admin, so a fresh
         open-source deployment can be administered before (or without) any
         LDAP directory being configured, and so an operator is never locked
         out if LDAP is misconfigured or unreachable. Disabled when
         ADMIN_PASSWORD is unset/empty.
      2. LDAP — the normal path for everyone else. Skipped cleanly when LDAP is
         not configured (unconfigured install → only the bootstrap admin works).
    """
    admin = _bootstrap_admin_authenticate(username, password)
    if admin is not None:
        return admin

    from . import ldap_config_service

    if not ldap_config_service.is_configured():
        # No directory configured — nothing else to try. (The bootstrap admin
        # is the only way in until an admin fills in LDAP via the Users tab.)
        logger.info("LDAP not configured; rejecting non-admin login for %r", username)
        return None

    return _ldap_authenticate(username, password)


def _bootstrap_admin_authenticate(username: str, password: str) -> dict | None:
    """Verify the built-in env-var admin. Returns user dict or None.

    Returns None (not a rejection) when the supplied username is not the admin
    username or when the bootstrap admin is disabled — so the caller falls
    through to LDAP. Only a *matching username with a wrong password* is a hard
    failure, and we signal that the same way (None) since the login endpoint
    treats None as "invalid credentials" regardless.
    """
    admin_user = (current_app.config.get("ADMIN_USERNAME") or "admin").strip()
    admin_pass = current_app.config.get("ADMIN_PASSWORD") or ""

    if not admin_pass:
        return None  # bootstrap admin disabled
    if (username or "").strip().lower() != admin_user.lower():
        return None  # not the admin account → let LDAP handle it

    # Constant-time compare to avoid leaking the password via timing.
    if not hmac.compare_digest(password or "", admin_pass):
        logger.warning("Bootstrap admin login failed: bad password")
        return None

    user_id = _upsert_bootstrap_admin(admin_user)
    _stash_password_hash(user_id, password)
    logger.info("Bootstrap admin %r logged in", admin_user)
    return {"id": user_id, "username": admin_user, "is_admin": True}


def _upsert_bootstrap_admin(username: str) -> int:
    """Ensure a local, active, admin users row exists for the bootstrap admin."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE username = %s", (username,))
            row = cur.fetchone()
            if row:
                cur.execute(
                    "UPDATE users SET auth_source='local', is_admin=1, "
                    "is_active=1, last_login_at=%s WHERE id=%s",
                    (now, row["id"]),
                )
                user_id = row["id"]
            else:
                cur.execute(
                    "INSERT INTO users "
                    "  (username, auth_source, display_name, is_admin, "
                    "   is_active, last_login_at) "
                    "VALUES (%s, 'local', %s, 1, 1, %s)",
                    (username, "Administrator", now),
                )
                user_id = cur.lastrowid
            conn.commit()
    finally:
        conn.close()
    return user_id


def _ldap_authenticate(username: str, password: str) -> dict | None:
    """Bind to LDAP, then upsert the user record in the DB."""
    from . import ldap_service, ldap_config_service

    ldap_user = ldap_service.ldap_bind(username, password)
    if not ldap_user:
        return None

    # Check admin group membership (case-insensitive DN comparison).
    # If get_user_groups returns None the LDAP query failed — treat as unknown
    # rather than silently demoting the user. Admin group comes from the
    # effective (DB > env) LDAP config, not directly from current_app.config.
    admin_group = (ldap_config_service.get_effective().get("admin_group") or "").lower()
    groups = ldap_service.get_user_groups(ldap_user["dn"])
    if groups is None:
        # LDAP group lookup failed; leave is_admin unchanged for existing users
        is_admin = None
    else:
        is_admin = 1 if admin_group in [g.lower() for g in groups] else 0

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, is_admin FROM users WHERE username = %s",
                (username,),
            )
            row = cur.fetchone()

            # If group lookup failed, preserve existing is_admin; default new users to 0
            effective_is_admin = is_admin if is_admin is not None else (row["is_admin"] if row else 0)

            if row:
                cur.execute(
                    """UPDATE users
                       SET ldap_dn=%s, email=%s, display_name=%s,
                           is_admin=%s, is_active=1, last_login_at=%s
                       WHERE username=%s""",
                    (ldap_user["dn"], ldap_user.get("email"),
                     ldap_user.get("display_name"), effective_is_admin, now, username),
                )
                user_id = row["id"]
            else:
                cur.execute(
                    """INSERT INTO users
                           (username, auth_source, ldap_dn, email,
                            display_name, is_admin, is_active, last_login_at)
                       VALUES (%s, 'ldap', %s, %s, %s, %s, 1, %s)""",
                    (username, ldap_user["dn"], ldap_user.get("email"),
                     ldap_user.get("display_name"), effective_is_admin, now),
                )
                user_id = cur.lastrowid

            conn.commit()
    finally:
        conn.close()

    # Refresh tenant memberships from the AD groups we already fetched above.
    # `groups` may be None (LDAP lookup failure) — group_service treats that
    # as "skip and leave existing memberships untouched".
    try:
        from . import group_service
        group_service.sync_user_groups_from_ad(user_id, groups)
    except Exception:
        logger.exception("Failed to sync tenant memberships for user_id=%s", user_id)

    # v0.14.2 — stash a SHA-512 crypt hash of the user's password in
    # Redis (TTL = JWT access-token TTL). Quick Create reads it back to
    # set the same CEC password on the resulting Linux VM, so the user
    # doesn't have to invent a new password per VM and doesn't have to
    # re-type their CEC password every create. See module docstring
    # above for the TTL / failure-mode reasoning.
    _stash_password_hash(user_id, password)

    return {"id": user_id, "username": username, "is_admin": bool(effective_is_admin)}


def issue_tokens(user_id: int) -> dict:
    """Issue JWT access and refresh tokens."""
    access_token = create_access_token(identity=str(user_id))
    refresh_token = create_refresh_token(identity=str(user_id))
    return {"access_token": access_token, "refresh_token": refresh_token}
