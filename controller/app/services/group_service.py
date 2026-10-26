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

"""Group service — sync a user's AD group memberships into `user_tenants`.

This is the binding layer between LDAP (where a user's `memberOf` list lives)
and the `tenants` table (which records the AD group DNs an admin has chosen
to expose as tenants). Only AD groups that match an existing tenant's
`ad_group_dn` produce membership rows; all other AD groups the user is in
are ignored.

The built-in `SHARED` tenant has `ad_group_dn = NULL` and is granted to every
authenticated user implicitly at visibility-check time — it never appears in
`user_tenants`.

Called from `auth_service._ldap_authenticate` on every successful login.
"""

import logging
import os
from datetime import datetime
from urllib.parse import urlparse

import pymysql
import pymysql.cursors

logger = logging.getLogger("wvc.group-service")


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


def sync_user_groups_from_ad(user_id: int, ldap_groups: list[str] | None) -> int:
    """Refresh the user's `user_tenants` rows from their LDAP group list.

    Args:
        user_id:     local users.id
        ldap_groups: list of AD group DNs the user is `memberOf`, or None
                     if the LDAP query failed (in which case we leave the
                     existing rows untouched — we'd rather show a stale view
                     than spuriously revoke access on a transient LDAP error)

    Returns:
        the number of tenants the user is now a member of (excluding SHARED,
        which is implicit). Returns -1 if the lookup was skipped due to None.
    """
    if ldap_groups is None:
        logger.info("user_id=%s: skipping group sync (LDAP query failed)", user_id)
        return -1

    # Build a lowercase set of the user's AD group DNs for case-insensitive matching
    user_group_set = {g.lower() for g in ldap_groups}

    conn = _get_db()
    try:
        with conn.cursor() as cur:
            # Load all non-builtin tenants that have an ad_group_dn set
            cur.execute(
                "SELECT id, ad_group_dn FROM tenants "
                "WHERE ad_group_dn IS NOT NULL AND is_builtin = 0"
            )
            matched_tenant_ids: list[int] = [
                row["id"] for row in cur.fetchall()
                if row["ad_group_dn"].lower() in user_group_set
            ]

            # Replace strategy: DELETE then INSERT. Simpler than a diff and the
            # row count per user is small (a handful at most).
            cur.execute(
                "DELETE FROM user_tenants WHERE user_id = %s",
                (user_id,),
            )
            if matched_tenant_ids:
                now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                cur.executemany(
                    "INSERT INTO user_tenants (user_id, tenant_id, synced_at) "
                    "VALUES (%s, %s, %s)",
                    [(user_id, tid, now) for tid in matched_tenant_ids],
                )
            conn.commit()

        logger.info(
            "user_id=%s synced from AD: %d AD group(s), matched %d tenant(s) %s",
            user_id, len(ldap_groups), len(matched_tenant_ids), matched_tenant_ids,
        )
        return len(matched_tenant_ids)
    finally:
        conn.close()


def sync_all_groups() -> None:
    """Periodic sync — refresh all active users' group memberships from AD.

    Not wired up yet. Will be called from a background thread similar to
    `vm_sync` / `node_monitor`. For now, login is the only sync trigger.
    """
    # TODO: implement periodic refresh (v0.10+)
    pass
