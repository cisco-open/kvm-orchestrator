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

"""HA runtime settings — authoritative on/off kill switch.

The config env var HA_ENABLED is only a startup default; the LIVE switch is a
row in the `ha_settings` table (migration 0008). The HA engine checks
`is_ha_enabled()` at the top of every heartbeat cycle so ops can flip HA from
the admin UI without a redeploy.

Raw pymysql to match the rest of the service layer.
"""

import logging
import os
from datetime import datetime
from urllib.parse import urlparse

import pymysql
import pymysql.cursors

logger = logging.getLogger("wvc.ha-settings")


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


def get_setting(name: str, default: str | None = None) -> str | None:
    """Return the string value of an ha_settings row, or `default`.

    Fail-safe: if the table doesn't exist yet (pre-migration) or the query
    errors, return `default` rather than raising — HA callers treat a failed
    read as "disabled".
    """
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM ha_settings WHERE name = %s", (name,))
            row = cur.fetchone()
            return row["value"] if row else default
    except Exception as e:  # table missing / DB blip → safe default
        logger.warning("ha_settings read failed for %s: %s", name, e)
        return default
    finally:
        conn.close()


def set_setting(name: str, value: str, updated_by: str | None = None) -> None:
    """Upsert an ha_settings row."""
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ha_settings (name, value, updated_at, updated_by) "
                "VALUES (%s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE value = VALUES(value), "
                "updated_at = VALUES(updated_at), updated_by = VALUES(updated_by)",
                (name, value, datetime.utcnow(), updated_by),
            )
        conn.commit()
        logger.info("ha_settings %s set to %s by %s", name, value, updated_by)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def is_ha_enabled() -> bool:
    """Authoritative runtime HA switch. Defaults to False (safe) if unset."""
    return (get_setting("ha_enabled", "false") or "false").lower() == "true"


def set_ha_enabled(enabled: bool, updated_by: str | None = None) -> None:
    set_setting("ha_enabled", "true" if enabled else "false", updated_by=updated_by)
