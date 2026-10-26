#!/usr/bin/env python3
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

"""Reconcile SHARED + unowned VMs to their AD owner.

Scans ``vms WHERE tenant_id = SHARED (1) AND owner_user_id IS NULL``. For
each row the VM name is split on ``-`` / ``_`` / ``.`` and every segment is
looked up in Active Directory by ``sAMAccountName`` (a real AD lookup, not
just against users cached in the local ``users`` table). If **exactly one**
distinct AD username matches, the VM is moved to the PERSONAL tenant
(id=3, DB name "ODaaSE" since the v0.21 merge) and ``owner_user_id`` is
set to that user. If the AD user has never logged in a stub ``users`` row
is inserted (``auth_source='ldap'``, ``is_admin=0``, ``last_login_at=NULL``)
so the FK is satisfied; the next real LDAP login of that user updates
their group membership and admin flag via the normal auth path.

Ambiguous VMs (two or more distinct AD usernames appearing in the name)
are logged at WARNING and left untouched. VMs with no usable name segments
or no AD match are logged at DEBUG and left untouched.

Safe to re-run: the UPDATE re-asserts ``tenant_id = SHARED AND
owner_user_id IS NULL`` so already-claimed rows are naturally skipped.

Required environment
--------------------
    DATABASE_URL        mysql+pymysql://user:pass@host:3306/db
    LDAP_URI            e.g. ldap://ldap.example.com:389
    LDAP_BASE_DN        e.g. DC=example,DC=com
    LDAP_BIND_DN        service-account DN or sAMAccountName
    LDAP_BIND_PASSWORD  service-account password

Optional
    LDAP_USER_FILTER    default (sAMAccountName={username})

Typical invocation inside a controller pod::

    kubectl -n kvm-orchestrator exec -it deploy/kvm-orchestrator-api -- \\
        python /app/scripts/reconcile_shared_vms.py --dry-run

Then re-run without --dry-run once the plan looks right.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from urllib.parse import urlparse

import pymysql
import pymysql.cursors
import ssl
from ldap3 import ALL, SUBTREE, Connection, Server, Tls
from ldap3.utils.conv import escape_filter_chars

# Mirror the built-ins in controller/app/services/tenant_service.py — kept
# as literals here so the script has no import dependency on the app.
# PERSONAL_TENANT_ID renumbered 2 → 3 in v0.21 (migration 0004 merged the
# old "Personal VM" tenant into "ODaaSE"). Rows written by this script
# will land on tenant_id=3 which is now the owner-only tenant.
SHARED_TENANT_ID = 1
PERSONAL_TENANT_ID = 3

# Segment splitter: anything a VM name typically uses as a delimiter.
_SPLIT_RE = re.compile(r"[-_.]+")

# Drop short / numeric segments — 'ai', 'v2', '04' etc. would cause noisy
# AD queries and, worse, false-positive matches on very short usernames.
_MIN_SEGMENT_LEN = 3

# Service / machine accounts that exist as sAMAccountName in AD but are
# never a legitimate VM owner. Segments matching these are treated as if
# AD returned no hit — prevents 'ccabreu-dhcp-test' from being flagged
# ambiguous just because 'test' resolves to the Test Scripting Account,
# and prevents a shared VM being wrongly claimed by that account.
_IGNORED_USERNAMES = frozenset({
    "test",       # CN=Test Scripting Account
    "builder",    # CN=Builder (machine account)
    "root",       # generic
    "admin",      # generic
    "automation", # CN=Automation Root
})

log = logging.getLogger("reconcile-shared-vms")


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------

def get_db_conn() -> pymysql.connections.Connection:
    """Open a raw pymysql connection from DATABASE_URL.

    Same shape as the ``_get_db`` helper duplicated across the service
    layer (see e.g. ``app/services/auth_service.py:_get_db``). We inline
    it here to keep the script Flask-free.
    """
    url = urlparse(os.environ["DATABASE_URL"])
    return pymysql.connect(
        host=url.hostname,
        port=url.port or 3306,
        user=url.username,
        password=url.password,
        database=url.path.lstrip("/"),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def ldap_connect() -> Connection:
    """Bind to AD as the service account and return the live Connection.

    Mirrors ``ldap_service.ldap_bind``'s service-account setup so we reuse
    the exact same Server/Connection tuning (``get_info=ALL``,
    ``connect_timeout=5``, ``auto_referrals=False``).
    """
    _uri = os.environ["LDAP_URI"]
    if _uri.lower().startswith("ldaps://"):
        server = Server(_uri, get_info=ALL, connect_timeout=5,
                        use_ssl=True, tls=Tls(validate=ssl.CERT_NONE))
    else:
        server = Server(_uri, get_info=ALL, connect_timeout=5)
    return Connection(
        server,
        user=os.environ["LDAP_BIND_DN"],
        password=os.environ["LDAP_BIND_PASSWORD"],
        auto_bind=True,
        auto_referrals=False,
    )


# ---------------------------------------------------------------------------
# Name → segments → AD user
# ---------------------------------------------------------------------------

def candidate_segments(vm_name: str) -> list[str]:
    """Return unique, lowercase, sanity-filtered segments of ``vm_name``.

    Order is preserved so logs match left-to-right reading of the name.
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in _SPLIT_RE.split(vm_name):
        seg = raw.strip().lower()
        if len(seg) < _MIN_SEGMENT_LEN:
            continue
        if seg.isdigit():
            continue
        if seg in seen:
            continue
        seen.add(seg)
        out.append(seg)
    return out


def lookup_ad_user(
    conn: Connection,
    base_dn: str,
    user_filter_tmpl: str,
    username: str,
    cache: dict[str, dict | None],
) -> dict | None:
    """AD search for ``sAMAccountName == username``. Returns the first hit.

    Cached across the whole run so we only pay one round-trip per distinct
    segment, no matter how many VMs contain it.
    """
    if username in cache:
        return cache[username]

    if username in _IGNORED_USERNAMES:
        cache[username] = None
        return None

    ad_filter = user_filter_tmpl.replace(
        "{username}", escape_filter_chars(username)
    )
    ok = conn.search(
        base_dn,
        ad_filter,
        search_scope=SUBTREE,
        attributes=["sAMAccountName", "mail", "displayName"],
    )
    result: dict | None = None
    if ok and conn.entries:
        entry = conn.entries[0]
        # AD echoes back canonical case for sAMAccountName — trust it over
        # whatever came out of the VM name.
        canonical = (
            str(entry["sAMAccountName"])
            if "sAMAccountName" in entry and entry["sAMAccountName"]
            else username
        )
        result = {
            "dn": entry.entry_dn,
            "username": canonical,
            "email": (
                str(entry.mail)
                if "mail" in entry and entry.mail
                else None
            ),
            "display_name": (
                str(entry.displayName)
                if "displayName" in entry and entry.displayName
                else None
            ),
        }
    cache[username] = result
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Assign SHARED+unowned VMs to AD owners by name match.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan but write nothing to the DB.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="Only process the first N candidate VMs (0 = all).",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="DEBUG-level logging (shows every VM scanned, not just matches).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        format="%(asctime)s %(levelname)-7s %(message)s",
        level=logging.DEBUG if args.verbose else logging.INFO,
    )

    missing = [
        k
        for k in (
            "DATABASE_URL",
            "LDAP_URI",
            "LDAP_BASE_DN",
            "LDAP_BIND_DN",
            "LDAP_BIND_PASSWORD",
        )
        if not os.environ.get(k)
    ]
    if missing:
        log.error("missing required env var(s): %s", ", ".join(missing))
        return 2

    user_filter_tmpl = os.environ.get(
        "LDAP_USER_FILTER", "(sAMAccountName={username})"
    )
    if "{username}" not in user_filter_tmpl:
        log.error("LDAP_USER_FILTER must contain '{username}' placeholder")
        return 2
    base_dn = os.environ["LDAP_BASE_DN"]

    log.info("Connecting to DB...")
    db = get_db_conn()
    log.info("Binding to LDAP at %s ...", os.environ["LDAP_URI"])
    ldap = ldap_connect()

    lookup_cache: dict[str, dict | None] = {}
    stats = {
        "scanned": 0,
        "no_candidate_segments": 0,
        "no_match": 0,
        "ambiguous": 0,
        "matched": 0,
        "updated": 0,
        "users_created": 0,
        "update_race": 0,
    }

    try:
        with db.cursor() as cur:
            cur.execute(
                """SELECT v.id, v.name, v.node_id,
                          n.hostname AS node_hostname
                     FROM vms v
                     JOIN nodes n ON n.id = v.node_id
                    WHERE v.tenant_id = %s
                      AND v.owner_user_id IS NULL
                    ORDER BY n.hostname, v.name""",
                (SHARED_TENANT_ID,),
            )
            vms = cur.fetchall()

        if args.limit > 0:
            vms = vms[: args.limit]
        log.info(
            "Found %d SHARED+unowned VM(s)%s",
            len(vms),
            f" (limited to first {args.limit})" if args.limit else "",
        )

        for vm in vms:
            stats["scanned"] += 1
            segments = candidate_segments(vm["name"])
            if not segments:
                stats["no_candidate_segments"] += 1
                log.debug("[%s] no usable name segments", vm["name"])
                continue

            # Collect distinct matches keyed on canonical username so the
            # same AD user showing up in two segments (unusual but possible)
            # still counts as one match, not two.
            matches: dict[str, dict] = {}
            for seg in segments:
                hit = lookup_ad_user(
                    ldap, base_dn, user_filter_tmpl, seg, lookup_cache
                )
                if hit:
                    matches[hit["username"].lower()] = hit

            if not matches:
                stats["no_match"] += 1
                log.debug(
                    "[%s] no AD match for segments %s",
                    vm["name"],
                    segments,
                )
                continue

            if len(matches) > 1:
                stats["ambiguous"] += 1
                log.warning(
                    "[%s] on %s: ambiguous - %d AD usernames match %s "
                    "(skipping)",
                    vm["name"],
                    vm["node_hostname"],
                    len(matches),
                    sorted(m["username"] for m in matches.values()),
                )
                continue

            stats["matched"] += 1
            ldap_user = next(iter(matches.values()))

            if args.dry_run:
                log.info(
                    "[%s] on %s -> WOULD claim for %s (%s)",
                    vm["name"],
                    vm["node_hostname"],
                    ldap_user["username"],
                    ldap_user["dn"],
                )
                continue

            # --- Real write path ---------------------------------------------
            with db.cursor() as cur:
                cur.execute(
                    "SELECT id FROM users WHERE username = %s",
                    (ldap_user["username"],),
                )
                row = cur.fetchone()
                if row:
                    user_id = row["id"]
                else:
                    cur.execute(
                        """INSERT INTO users
                               (username, auth_source, ldap_dn, email,
                                display_name, is_admin, is_active,
                                last_login_at)
                           VALUES (%s, 'ldap', %s, %s, %s, 0, 1, NULL)""",
                        (
                            ldap_user["username"],
                            ldap_user["dn"],
                            ldap_user["email"],
                            ldap_user["display_name"],
                        ),
                    )
                    user_id = cur.lastrowid
                    stats["users_created"] += 1
                    log.info(
                        "created stub users row id=%d for %s (%s)",
                        user_id,
                        ldap_user["username"],
                        ldap_user["dn"],
                    )

                # Re-assert SHARED+unowned in the WHERE so any concurrent
                # claim (v0.11.1 claim flow, admin bulk-assign, etc.) wins
                # over us cleanly rather than being clobbered.
                cur.execute(
                    """UPDATE vms
                          SET tenant_id = %s,
                              owner_user_id = %s
                        WHERE id = %s
                          AND tenant_id = %s
                          AND owner_user_id IS NULL""",
                    (
                        PERSONAL_TENANT_ID,
                        user_id,
                        vm["id"],
                        SHARED_TENANT_ID,
                    ),
                )
                if cur.rowcount == 1:
                    stats["updated"] += 1
                    log.info(
                        "[%s] on %s -> claimed by %s (users.id=%d)",
                        vm["name"],
                        vm["node_hostname"],
                        ldap_user["username"],
                        user_id,
                    )
                else:
                    stats["update_race"] += 1
                    log.warning(
                        "[%s] UPDATE affected 0 rows (someone else "
                        "claimed it first?) - skipping",
                        vm["name"],
                    )
            db.commit()

        # ------------------------------------------------------------------
        log.info("=" * 60)
        log.info("SUMMARY (dry_run=%s)", args.dry_run)
        for k, v in stats.items():
            log.info("  %-22s %d", k, v)
        return 0
    except Exception:
        log.exception("aborting - rolling back current transaction")
        try:
            db.rollback()
        except Exception:
            pass
        return 1
    finally:
        try:
            ldap.unbind()
        except Exception:
            pass
        db.close()


if __name__ == "__main__":
    sys.exit(main())
