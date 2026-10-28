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

"""VM lifecycle notifications (Webex).

Reuses the HA Webex config (WEBEX_BOT_TOKEN + WEBEX_HA_ROOM_ID). Two channels:

  * DM the VM owner directly (toPersonEmail = {username}@<EMAIL_DOMAIN>) with the
    expiry warning + a deep link to renew.
  * Post an Ops-room summary when the checker auto-stops or auto-deletes a VM.

Notify-only and best-effort: a Webex failure never blocks the lifecycle action.
ENVIRONMENT=test prefixes messages with [***TEST***].
"""

import logging

import requests
from flask import current_app

logger = logging.getLogger("wvc.lifecycle-notify")

_API = "https://webexapis.com/v1/messages"


def _cfg():
    c = current_app.config
    return (c.get("WEBEX_BOT_TOKEN"), c.get("WEBEX_HA_ROOM_ID"),
            "[***TEST***] " if c.get("ENVIRONMENT", "live") == "test" else "",
            (c.get("WEBEX_DEEP_LINK_BASE", "") or "").rstrip("/"))


def _post(payload: dict, what: str) -> bool:
    token, _room, _prefix, _base = _cfg()
    if not token:
        logger.info("Webex not configured — skipping %s", what)
        return False
    try:
        resp = requests.post(
            _API, headers={"Authorization": f"Bearer {token}"},
            json=payload, timeout=10)
        if resp.status_code // 100 != 2:
            logger.warning("Webex %s failed %s: %s", what, resp.status_code,
                           resp.text[:200])
            return False
        return True
    except Exception:
        logger.exception("Webex %s error (non-fatal)", what)
        return False


def _email_domain() -> str:
    """Org mail domain for deriving addresses from usernames. Configurable via
    EMAIL_DOMAIN (Flask config, else env), default example.com."""
    try:
        from flask import current_app
        d = current_app.config.get("EMAIL_DOMAIN")
        if d:
            return d
    except Exception:
        pass
    import os
    return os.environ.get("EMAIL_DOMAIN", "example.com")


def owner_email(owner_username: str | None) -> str | None:
    """Best-effort owner email. Prefer the users.email column; fall back to
    {username}@<EMAIL_DOMAIN> (the username-as-local-part convention)."""
    if not owner_username:
        return None
    try:
        from .vm_lifecycle import _db
        conn = _db()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT email FROM users WHERE username = %s",
                            (owner_username,))
                row = cur.fetchone()
                if row and row.get("email"):
                    return row["email"]
        finally:
            conn.close()
    except Exception:
        logger.debug("owner_email lookup failed for %s", owner_username, exc_info=True)
    # strip a .root suffix for the username local-part
    cec = owner_username.split(".")[0] if owner_username else owner_username
    return f"{cec}@{_email_domain()}" if cec else None


def _email_for_row(username: str | None, email: str | None) -> str | None:
    """Resolve a mailable address from a users row: prefer stored email,
    else {username}@<EMAIL_DOMAIN>."""
    if email:
        return email
    if username:
        cec = username.split(".")[0]
        return f"{cec}@{_email_domain()}" if cec else None
    return None


def recipient_emails(owner_username: str | None, tenant_id: int | None) -> list[str]:
    """Everyone who should be warned about a VM's expiry.

    Rule:
      * Always the VM owner (if any).
      * If the VM belongs to a REAL tenant (admin-created, bound to an AD group
        — i.e. NOT a built-in SHARED/PERSONAL/DEFAULT tenant), also every member
        of that tenant's AD group. Membership is resolved LIVE from AD via
        the tenant's `ad_group_dn` (authoritative), because the local
        `user_tenants` cache is only populated when a member logs in — a tenant
        whose members have never logged in would otherwise notify nobody.
        If the AD query fails we fall back to the local `user_tenants` cache.
        Built-in tenants (SHARED = "everyone") are not membership groups, so we
        do NOT expand them.

    Returns a de-duplicated, order-stable list of email addresses. Best-effort.
    """
    emails: list[str] = []
    seen: set[str] = set()

    def _add(addr: str | None):
        if addr and addr.lower() not in seen:
            seen.add(addr.lower())
            emails.append(addr)

    _add(owner_email(owner_username))

    try:
        from .tenant_service import BUILTIN_TENANT_IDS
    except Exception:
        BUILTIN_TENANT_IDS = (1, 3, 4)

    if tenant_id and tenant_id not in BUILTIN_TENANT_IDS:
        # Look up the tenant's AD group DN (real tenants only).
        ad_group_dn = None
        try:
            from .vm_lifecycle import _db
            conn = _db()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT ad_group_dn FROM tenants "
                        "WHERE id = %s AND is_builtin = 0",
                        (tenant_id,),
                    )
                    row = cur.fetchone()
                    ad_group_dn = (row or {}).get("ad_group_dn")
            finally:
                conn.close()
        except Exception:
            logger.debug("tenant ad_group_dn lookup failed for tenant=%s",
                         tenant_id, exc_info=True)

        member_emails: list[str] | None = None
        if ad_group_dn:
            # Authoritative: query AD for the group's members.
            try:
                from .ldap_service import get_group_member_emails
                member_emails = get_group_member_emails(ad_group_dn)
            except Exception:
                logger.debug("AD group member lookup failed for %s",
                             ad_group_dn, exc_info=True)
                member_emails = None

        if member_emails is None:
            # AD unavailable (or no ad_group_dn) — fall back to the local
            # user_tenants cache so we still notify whoever we know about.
            try:
                from .vm_lifecycle import _db
                conn = _db()
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT u.username, u.email "
                            "FROM user_tenants ut "
                            "JOIN users u ON u.id = ut.user_id "
                            "JOIN tenants t ON t.id = ut.tenant_id "
                            "WHERE ut.tenant_id = %s AND t.is_builtin = 0",
                            (tenant_id,),
                        )
                        member_emails = [
                            _email_for_row(r.get("username"), r.get("email"))
                            for r in (cur.fetchall() or [])
                        ]
                finally:
                    conn.close()
            except Exception:
                logger.debug("user_tenants fallback failed for tenant=%s",
                             tenant_id, exc_info=True)
                member_emails = []

        for addr in member_emails or []:
            _add(addr)

    return emails


def warn_owner(vm_name: str, owner_username: str | None, *, stage: str,
               days_left: float | None, expires_at,
               tenant_id: int | None = None, vm_id: int | None = None) -> bool:
    """DM the VM owner AND (for real tenants) every tenant member an expiry
    warning. `stage` is one of 'pre1' | 'pre2' | 'stopped' | 'poststop'.

    The Webex message includes a deep-link that opens the VMs page focused on
    this VM with the Renew action highlighted (?renew=<vm_id>)."""
    _t, room, prefix, base = _cfg()
    recipients = recipient_emails(owner_username, tenant_id)
    # Deep-link straight to this VM's Renew action.
    if base and vm_id is not None:
        link = f"{base}/vms?renew={vm_id}"
    elif base:
        link = f"{base}/vms"
    else:
        link = ""

    # Owned-by-a-tenant VMs use "This" rather than "Your" since not every
    # recipient personally owns it.
    possessive = "Your" if not tenant_id else "This"

    if stage in ("pre1", "pre2"):
        head = f"{prefix}⏰ **VM expiring — `{vm_name}`**"
        body = (f"- {possessive} VM reaches its 1-year term on **{expires_at}** "
                f"(~{int(days_left)} day(s) left).\n"
                f"- **Renew** to extend it another 12 months. If not renewed it "
                f"will be **shut down** at expiry and cannot be restarted until "
                f"renewed.\n")
    elif stage == "stopped":
        head = f"{prefix}🛑 **VM shut down (expired) — `{vm_name}`**"
        body = (f"- {possessive} VM reached its 1-year term and has been "
                f"**shut down**.\n"
                "- It **cannot be restarted** until you **Renew** it. Renewing "
                "extends it 12 months and powers it back on.\n")
    else:  # poststop (repeat of the shut-down notice, ~12.5 mo)
        head = f"{prefix}⚠️ **VM still expired — `{vm_name}`**"
        body = (f"- {possessive} VM is still shut down after expiry and remains "
                "unusable until renewed.\n"
                "- **Renew** to restore it. If left unrenewed it will be flagged "
                "to Ops for deletion.\n")

    md = head + "\n\n" + body
    if link:
        md += f"\n[Renew `{vm_name}` in KVM Orchestrator]({link})"

    # DM every recipient (owner + real-tenant members). If there are none
    # (infra VM with no owner), drop a copy in the Ops room so nothing goes
    # unnoticed. Best-effort per recipient; overall success = at least one sent.
    if recipients:
        sent_any = False
        for email in recipients:
            if _post({"toPersonEmail": email, "markdown": md}, f"warn:{stage}"):
                sent_any = True
        return sent_any
    if room:
        return _post({"roomId": room, "markdown": f"{md}\n\n_(no owner on record)_"},
                     f"warn:{stage}:ops")
    return False


def ops_summary(action: str, vm_name: str, owner_username: str | None) -> bool:
    """Post an auto-stop summary to the Ops room."""
    _t, room, prefix, base = _cfg()
    if not room:
        return False
    md = (f"{prefix}🛑 **VM auto-stopped (expired) — `{vm_name}`**\n\n"
          f"- Owner: {owner_username or '_none (infra)_'}\n"
          f"- Shut down by the lifecycle checker (1-year TTL). It cannot be "
          f"restarted until the owner renews.")
    return _post({"roomId": room, "markdown": md}, f"ops:{action}")


def ops_delete_request(vm_name: str, owner_username: str | None,
                       vm_id: int | None = None) -> bool:
    """Post an Ops-room DELETE REQUEST for a VM that has been expired/stopped
    for the full grace period without renewal. The VM is NOT auto-deleted —
    this asks an admin to action deletion, with a deep-link to the VM. Renewing
    is still possible (link included) if the owner comes back.
    """
    _t, room, prefix, base = _cfg()
    if not room:
        return False
    vm_link = f"{base}/vms?vm={vm_id}" if (base and vm_id is not None) else (
        f"{base}/vms" if base else "")
    md = (f"{prefix}🗑️ **VM expired — admin deletion requested — `{vm_name}`**\n\n"
          f"- Owner: {owner_username or '_none (infra)_'}\n"
          f"- This VM has been shut down and unrenewed past its grace period "
          f"(1-year TTL). It was **not** auto-deleted.\n"
          f"- **Action for an admin:** review and delete it, or renew it if it "
          f"is still needed.\n")
    if vm_link:
        md += f"\n[Open `{vm_name}` in KVM Orchestrator]({vm_link})"
    return _post({"roomId": room, "markdown": md}, "ops:delete_request")
