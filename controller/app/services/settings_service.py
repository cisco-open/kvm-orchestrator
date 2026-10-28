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

"""Generic app_settings key/value store with at-rest encryption for secrets.

This is the DB-backed, admin-editable, redeploy-free settings store used for
things that must be reconfigurable at runtime by an admin in the UI — most
importantly the LDAP directory config (see ``ldap_config_service``).

It follows the same shape as ``ha_settings_service`` (raw pymysql, fail-safe
reads) but adds:

  * a distinct table (``app_settings``) so LDAP/auth config is not tangled up
    with the HA kill-switch rows, and
  * transparent encryption of values flagged secret (e.g. the LDAP bind
    password), so a DB dump never leaks the directory service-account password.

Encryption
----------
Secret values are sealed with Fernet (AES-128-CBC + HMAC). The key is DERIVED
from ``FLASK_SECRET_KEY`` — no new env var to manage. Consequence: rotating
``FLASK_SECRET_KEY`` invalidates stored secrets (they must be re-entered in the
UI). Non-secret values are stored verbatim.

The ``is_secret`` column records which rows are sealed so reads know whether to
decrypt, and so the API can mask secret values instead of returning them.
"""

import base64
import hashlib
import logging
import os
from datetime import datetime
from urllib.parse import urlparse

import pymysql
import pymysql.cursors
from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

logger = logging.getLogger("wvc.settings")

# Sentinel returned by get_setting for secret rows when decryption fails
# (e.g. FLASK_SECRET_KEY was rotated). Distinct from None (row absent).
DECRYPT_FAILED = object()


def _get_db():
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


def _fernet() -> Fernet:
    """Build a Fernet from FLASK_SECRET_KEY.

    A Fernet key must be 32 url-safe-base64 bytes; we derive that
    deterministically from FLASK_SECRET_KEY with SHA-256 so any secret string
    works as the app key without a separate, format-constrained env var.
    """
    try:
        secret = current_app.config.get("SECRET_KEY", "") or ""
    except RuntimeError:  # outside app context (shouldn't happen on the API path)
        secret = os.environ.get("FLASK_SECRET_KEY", "")
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def _decrypt(token: str):
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        logger.error(
            "Failed to decrypt a secret app_setting — FLASK_SECRET_KEY may have "
            "been rotated. The affected value must be re-entered in the admin UI."
        )
        return DECRYPT_FAILED


def get_setting(name: str, default: str | None = None):
    """Return the (decrypted) value of an app_settings row, or ``default``.

    Fail-safe: a missing table or DB error returns ``default`` rather than
    raising. For a secret row whose decryption fails, returns
    ``DECRYPT_FAILED`` so callers can tell "key rotated / corrupt" apart from
    "unset".
    """
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT value, is_secret FROM app_settings WHERE name = %s",
                (name,),
            )
            row = cur.fetchone()
        if not row:
            return default
        if row["is_secret"]:
            return _decrypt(row["value"])
        return row["value"]
    except Exception as e:  # table missing / DB blip → safe default
        logger.warning("app_settings read failed for %s: %s", name, e)
        return default
    finally:
        conn.close()


def is_secret_set(name: str) -> bool:
    """True if a secret row exists and is non-empty (without revealing it).

    Used by the API to tell the UI "a bind password is configured" without
    ever returning the plaintext.
    """
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT value FROM app_settings WHERE name = %s AND is_secret = 1",
                (name,),
            )
            row = cur.fetchone()
        return bool(row and row["value"])
    except Exception:
        return False
    finally:
        conn.close()


def set_setting(
    name: str, value: str, updated_by: str | None = None, is_secret: bool = False
) -> None:
    """Upsert an app_settings row. Secret values are encrypted at rest."""
    stored = _encrypt(value) if is_secret else value
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO app_settings "
                "  (name, value, is_secret, updated_at, updated_by) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE value = VALUES(value), "
                "  is_secret = VALUES(is_secret), "
                "  updated_at = VALUES(updated_at), updated_by = VALUES(updated_by)",
                (name, stored, 1 if is_secret else 0, datetime.utcnow(), updated_by),
            )
        conn.commit()
        logger.info(
            "app_settings %s updated by %s (secret=%s)", name, updated_by, is_secret
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
