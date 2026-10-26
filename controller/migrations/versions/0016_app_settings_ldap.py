# SPDX-License-Identifier: Apache-2.0
#
# Copyright [2026] [Cisco Systems, Inc. and its affiliates]
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

"""add app_settings store + seed LDAP config from env (open-source auth)

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-25

Introduces a generic, admin-editable, redeploy-free settings table used to hold
the LDAP directory configuration so an open-source deployment can be configured
entirely from the admin UI (Users tab) rather than requiring the operator to set
LDAP_* env vars and redeploy.

  app_settings(
    name       VARCHAR(64) PRIMARY KEY,
    value      TEXT NOT NULL,          -- encrypted when is_secret=1
    is_secret  TINYINT(1) NOT NULL DEFAULT 0,
    updated_at DATETIME NULL,
    updated_by VARCHAR(255) NULL
  )

Seeding (first migrate only): the historical LDAP_* env vars become the SEED for
the DB rows so an existing deployment keeps working unchanged; after
that the DB rows are authoritative and the env vars are ignored at runtime. The
bind password is sealed with Fernet using a key derived from FLASK_SECRET_KEY
(same scheme as app.services.settings_service) so it is never stored in the clear.

Idempotent: guards CREATE TABLE and each seed INSERT (INSERT ... WHERE NOT EXISTS).
"""
import base64
import hashlib
import os
from typing import Sequence, Union

from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# LDAP setting keys stored in app_settings. Kept in one place so the service
# layer and this migration agree. (name, env_var, is_secret)
_LDAP_KEYS = [
    ("ldap_enabled",        "LDAP_ENABLED",        False),
    ("ldap_uri",            "LDAP_URI",            False),
    ("ldap_base_dn",        "LDAP_BASE_DN",        False),
    ("ldap_bind_dn",        "LDAP_BIND_DN",        False),
    ("ldap_bind_password",  "LDAP_BIND_PASSWORD",  True),
    ("ldap_user_filter",    "LDAP_USER_FILTER",    False),
    ("ldap_group_base_dn",  "LDAP_GROUP_BASE_DN",  False),
    ("ldap_admin_group",    "LDAP_ADMIN_GROUP",    False),
]


def _seal(plaintext: str) -> str:
    """Fernet-encrypt using a key derived from FLASK_SECRET_KEY.

    Mirrors app.services.settings_service._fernet so values sealed here can be
    read back by the running app. Migrations run outside the Flask app context,
    so we read FLASK_SECRET_KEY from the environment directly.
    """
    from cryptography.fernet import Fernet

    secret = os.environ.get("FLASK_SECRET_KEY", "")
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    f = Fernet(base64.urlsafe_b64encode(digest))
    return f.encrypt(plaintext.encode("utf-8")).decode("ascii")


def upgrade() -> None:
    bind = op.get_bind()

    tables = {
        row[0]
        for row in bind.exec_driver_sql(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = DATABASE()"
        )
    }
    if "app_settings" not in tables:
        op.execute(
            "CREATE TABLE `app_settings` ("
            "  `name` VARCHAR(64) NOT NULL PRIMARY KEY,"
            "  `value` TEXT NOT NULL,"
            "  `is_secret` TINYINT(1) NOT NULL DEFAULT 0,"
            "  `updated_at` DATETIME NULL,"
            "  `updated_by` VARCHAR(255) NULL"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        )

    # Seed each LDAP key from its env var the FIRST time only. Empty env values
    # are still seeded (as empty rows) so the admin UI shows the full form; the
    # effective-config resolver treats blank rows as "unset" and can fall back.
    for name, env_var, is_secret in _LDAP_KEYS:
        raw = os.environ.get(env_var, "")
        stored = _seal(raw) if (is_secret and raw) else raw
        # For an empty secret, store empty (not sealed) so is_secret_set() reports unset.
        secret_flag = 1 if is_secret else 0
        # Bind params to avoid quoting issues in DNs/passwords.
        bind.exec_driver_sql(
            "INSERT INTO `app_settings` "
            "  (`name`, `value`, `is_secret`, `updated_at`, `updated_by`) "
            "SELECT %s, %s, %s, NOW(), 'migration-0016' "
            "FROM DUAL WHERE NOT EXISTS "
            "  (SELECT 1 FROM `app_settings` WHERE `name` = %s)",
            (name, stored, secret_flag, name),
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS `app_settings`")
