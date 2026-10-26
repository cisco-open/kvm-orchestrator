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

"""LDAP service — connection, search, group queries."""

import ssl

from ldap3 import Server, Connection, ALL, SUBTREE, Tls
from ldap3.utils.conv import escape_filter_chars

from ..metrics import ldap_binds_total


def _build_server(uri: str) -> Server:
    """Build an ldap3 Server. For ldaps:// URIs, wrap in TLS with certificate
    verification DISABLED (the DC presents a self-signed cert). This still
    ENCRYPTS the channel so bind credentials are not sent in cleartext — it just
    doesn't validate the cert chain. Plain ldap:// is left unencrypted.
    """
    if uri.lower().startswith("ldaps://"):
        tls = Tls(validate=ssl.CERT_NONE)
        return Server(uri, get_info=ALL, connect_timeout=5, use_ssl=True, tls=tls)
    return Server(uri, get_info=ALL, connect_timeout=5)


def ldap_bind(username: str, password: str) -> dict | None:
    """
    Attempt LDAP bind with user credentials.
    Returns user attributes dict on success, None on failure.

    Reads the EFFECTIVE LDAP config (DB app_settings row > env seed) via
    ldap_config_service so an admin can reconfigure the directory at runtime.
    """
    from . import ldap_config_service

    cfg = ldap_config_service.get_effective()
    uri = cfg["uri"]
    base_dn = cfg["base_dn"]
    user_filter = cfg["user_filter"].replace(
        "{username}", escape_filter_chars(username)
    )

    try:
        server = _build_server(uri)
        # First, find the user DN using service account
        service_conn = Connection(
            server,
            user=cfg["bind_dn"],
            password=cfg["bind_password"],
            auto_bind=True,
            auto_referrals=False,
        )
        ldap_binds_total.labels(kind="service", result="ok").inc()
        service_conn.search(base_dn, user_filter, search_scope=SUBTREE,
                            attributes=["mail", "displayName"])

        if not service_conn.entries:
            # Bind succeeded but user filter matched nothing — count as a
            # user-bind failure since that's the caller-visible outcome.
            ldap_binds_total.labels(kind="user", result="fail").inc()
            return None

        user_dn = service_conn.entries[0].entry_dn

        # Now bind as the user to verify password
        user_conn = Connection(server, user=user_dn, password=password,
                               auto_bind=True, auto_referrals=False)
        if user_conn.bound:
            ldap_binds_total.labels(kind="user", result="ok").inc()
            entry = service_conn.entries[0]
            return {
                "dn": user_dn,
                "username": username,
                "email": str(entry.mail) if entry.mail else None,
                "display_name": str(entry.displayName) if entry.displayName else None,
            }
        # Rare: auto_bind=True usually raises on failure, but be defensive.
        ldap_binds_total.labels(kind="user", result="fail").inc()
    except Exception:
        # ldap3 raises on bad password + on server errors. We can't
        # cleanly distinguish "service bind failed" from "user bind failed"
        # here without restructuring, so label the failure as "user" — the
        # login-visible outcome — and rely on service-bind counter only
        # tracking successes.
        ldap_binds_total.labels(kind="user", result="fail").inc()
        return None

    return None


def get_user_groups(user_dn: str) -> list[str] | None:
    """Query AD for all group DNs a user belongs to.

    Returns a list of group DNs, or None if the query failed (so callers
    can distinguish 'user has no groups' from 'query error').
    """
    from . import ldap_config_service

    cfg = ldap_config_service.get_effective()
    uri = cfg["uri"]
    group_base = cfg["group_base_dn"]

    try:
        server = _build_server(uri)
        conn = Connection(
            server,
            user=cfg["bind_dn"],
            password=cfg["bind_password"],
            auto_bind=True,
            auto_referrals=False,
        )
        conn.search(
            group_base,
            f"(member={escape_filter_chars(user_dn)})",
            search_scope=SUBTREE,
        )
        ldap_binds_total.labels(kind="group_query", result="ok").inc()
        return [str(entry.entry_dn) for entry in conn.entries]
    except Exception:
        ldap_binds_total.labels(kind="group_query", result="fail").inc()
        return None


def get_group_member_emails(group_dn: str) -> list[str] | None:
    """Query AD for the email addresses of every user in a group.

    Resolves the group's `member` DNs to their `mail` attribute. Handles the
    common case (direct membership) and one level of nested groups (a member DN
    that is itself a group is expanded once). Returns a de-duplicated list of
    emails, or None if the query failed (so callers can fall back to the local
    `user_tenants` cache rather than notify nobody).
    """
    from . import ldap_config_service

    cfg = ldap_config_service.get_effective()
    uri = cfg["uri"]
    base_dn = cfg["base_dn"]

    def _emails_for_group(conn, gdn: str, depth: int) -> list[str]:
        conn.search(gdn, "(objectClass=*)", search_scope="BASE",
                    attributes=["member"])
        if not conn.entries:
            return []
        members = conn.entries[0].member.values if conn.entries[0].member else []
        out: list[str] = []
        for m in members:
            # Resolve each member DN — could be a user or a nested group.
            conn.search(m, "(objectClass=*)", search_scope="BASE",
                        attributes=["mail", "sAMAccountName", "objectClass"])
            if not conn.entries:
                continue
            e = conn.entries[0]
            ocs = [str(x).lower() for x in (e.objectClass.values if e.objectClass else [])]
            if "group" in ocs and depth > 0:
                out.extend(_emails_for_group(conn, m, depth - 1))
                continue
            # Prefer a populated mail attribute; otherwise fall back to the
            # {sAMAccountName}@<EMAIL_DOMAIN> convention (AD often leaves
            # `mail` empty).
            if e.mail:
                out.append(str(e.mail))
            elif "sAMAccountName" in e and e.sAMAccountName:
                import os
                try:
                    from flask import current_app
                    _dom = current_app.config.get("EMAIL_DOMAIN") or os.environ.get("EMAIL_DOMAIN", "example.com")
                except Exception:
                    _dom = os.environ.get("EMAIL_DOMAIN", "example.com")
                out.append(f"{str(e.sAMAccountName)}@{_dom}")
        return out

    try:
        server = _build_server(uri)
        conn = Connection(
            server,
            user=cfg["bind_dn"],
            password=cfg["bind_password"],
            auto_bind=True,
            auto_referrals=False,
        )
        emails = _emails_for_group(conn, group_dn, depth=1)
        ldap_binds_total.labels(kind="group_query", result="ok").inc()
        # de-dup, case-insensitive, order-stable
        seen: set[str] = set()
        result: list[str] = []
        for a in emails:
            if a and a.lower() not in seen:
                seen.add(a.lower())
                result.append(a)
        return result
    except Exception:
        ldap_binds_total.labels(kind="group_query", result="fail").inc()
        return None
