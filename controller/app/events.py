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

"""User-facing event log + request context (v0.35.7).

Writes one plain-English line per thing a human did, with who and from where,
in a plain, human-readable style::

    alice started VM web-01 on node-1 from 203.0.113.10 [9f1c2e4a]
    admin changed LDAP settings (server_uri, base_dn) from 203.0.113.10 [77ab0c12]
    bob failed to migrate VM web-02 to node-3 from 198.51.100.7: target full (HTTP 400) [1d2e3f4a]

Everything else (GET polling, refresh, agent heartbeats) is a DEBUG access
line; any 5xx is a WARNING with context. /healthz and /metrics are silent.

Never logged: Authorization headers, tokens, passwords, raw request bodies.
Only whitelisted scalar fields (VM name, priority, target node...) and the
*names* of edited fields are read from bodies.

Client IP: X-Forwarded-For walked right to left from the socket peer,
skipping trusted proxies (``TRUSTED_PROXIES`` env, comma-separated CIDRs,
default "10.42.0.0/16,127.0.0.1" = the k3s/RKE2 default pod CIDR + loopback;
set it to your cluster's pod CIDR / ingress addresses); the first untrusted
hop is the client. A spoofed left-most entry is ignored.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import time
import uuid

from flask import g, has_request_context, request

logger = logging.getLogger("wvc.events")
access = logging.getLogger("wvc.access")

DEFAULT_TRUSTED_PROXIES = "10.42.0.0/16,127.0.0.1"


def parse_trusted_proxies(value: str | None) -> tuple:
    """Parse a comma-separated CIDR/IP list; invalid entries are skipped."""
    nets = []
    for part in (value if value is not None else DEFAULT_TRUSTED_PROXIES).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            logging.getLogger("wvc.events").warning("TRUSTED_PROXIES: ignoring invalid entry %r", part)
    return tuple(nets)


TRUSTED_PROXIES = parse_trusted_proxies(os.environ.get("TRUSTED_PROXIES"))
SILENT_PATHS = frozenset({"/healthz", "/metrics"})
USER_HEADER = "X-WVC-User"  # internal: UI nginx logs it, then strips it

_REQ_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_SECRETISH = re.compile(r"pass|secret|token|key|hash|cred", re.I)
_CACHE: dict[tuple[str, int], tuple[str, float]] = {}
_TTL = 300.0


# --------------------------------------------------------------------------- context
def _trusted(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in TRUSTED_PROXIES)


def client_ip() -> str:
    if not has_request_context():
        return "-"
    hops = [h.strip() for h in request.headers.get("X-Forwarded-For", "").split(",") if h.strip()]
    hops.append(request.remote_addr or "unknown")
    for hop in reversed(hops):
        if not _trusted(hop):
            return hop
    return hops[0]


def request_id() -> str:
    if not has_request_context():
        return "-"
    rid = getattr(g, "request_id", None)
    if rid is None:
        hdr = request.headers.get("X-Request-ID", "")
        rid = hdr if _REQ_ID_RE.match(hdr) else uuid.uuid4().hex[:16]
        g.request_id = rid
    return rid


def _lookup(table: str, col: str, row_id) -> str | None:
    """Cached single-column lookup by id (users/vms/nodes/tenants)."""
    try:
        row_id = int(row_id)
    except (TypeError, ValueError):
        return None
    key, now = (table, row_id), time.monotonic()
    hit = _CACHE.get(key)
    if hit and hit[1] > now:
        return hit[0]
    val = None
    try:
        from .services.tenant_service import _get_db

        conn = _get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {col} AS v FROM {table} WHERE id = %s", (row_id,))  # noqa: S608 (fixed identifiers)
                row = cur.fetchone()
                val = row["v"] if row else None
        finally:
            conn.close()
    except Exception:
        return None
    if val is not None:
        _CACHE[key] = (str(val), now + _TTL)
    return None if val is None else str(val)


def username_for(user_id) -> str:
    return _lookup("users", "username", user_id) or (f"user#{user_id}" if user_id not in (None, "-") else "-")


def current_user() -> tuple[str, str]:
    """(username, user_id) from MCP impersonation or a *verified* access JWT."""
    if not has_request_context():
        return "-", "-"
    if hasattr(g, "_wvc_user"):
        return g._wvc_user
    uid = getattr(g, "mcp_impersonated_user_id", None)
    if uid is None:
        try:
            from flask_jwt_extended import get_jwt, get_jwt_identity, verify_jwt_in_request

            verify_jwt_in_request(optional=True)
            if get_jwt().get("type") == "access":
                uid = get_jwt_identity()
        except Exception:
            uid = None
    out = ("-", "-") if uid is None else (username_for(uid), str(uid))
    g._wvc_user = out
    return out


def event(msg: str, *args, level: int = logging.INFO, ip: str | None = None, detail: str = "") -> None:
    """Log ``msg % args`` + ' from <ip>' + detail + ' [rid]' (context parts only when known)."""
    text = (msg % args if args else msg).replace("\r", " ").replace("\n", " ")
    ip = ip or client_ip()
    if ip != "-":
        text += f" from {ip}"
    text += detail.replace("\r", " ").replace("\n", " ")
    rid = request_id()
    if rid != "-":
        text += f" [{rid}]"
    logger.log(level, text)


# --------------------------------------------------------------------------- route -> sentence
def _body() -> dict:
    b = request.get_json(silent=True)
    return b if isinstance(b, dict) else {}


def _vm() -> str:
    return getattr(g, "_wvc_vm", None) or f"#{(request.view_args or {}).get('vm_id', '?')}"


def _node_arg() -> str:
    nid = (request.view_args or {}).get("node_id")
    return _lookup("nodes", "hostname", nid) or f"#{nid}"


def _fields() -> str:
    names = sorted(k for k in _body() if not _SECRETISH.search(k))
    return f" ({', '.join(names)})" if names else ""


def _on_node() -> str:
    node = getattr(g, "_wvc_vm_node", None)
    return f" on {node}" if node else ""


def _stop() -> str:
    return ("force off VM " if _body().get("force") else "stop VM ") + _vm() + _on_node()


def _migrate() -> str:
    tgt = _body().get("target_node_id")
    return f"migrate VM {_vm()} from {getattr(g, '_wvc_vm_node', '?')} to {_lookup('nodes', 'hostname', tgt) or tgt}"


def _tenant_name(tid) -> str:
    return _lookup("tenants", "name", tid) or str(tid)


_PAST = {
    "log out": "logged out", "force off": "forced off", "create": "created", "start": "started",
    "stop": "stopped", "reboot": "rebooted", "suspend": "suspended", "resume": "resumed",
    "delete": "deleted", "migrate": "migrated", "edit": "edited", "open": "opened",
    "renew": "renewed", "exempt": "exempted", "remove": "removed", "set": "set", "move": "moved",
    "attach": "attached", "detach": "detached", "change": "changed", "test": "tested",
    "add": "added", "put": "put", "trigger": "triggered", "fence": "fenced", "readmit": "readmitted",
}


def _past(phrase: str) -> str:
    for inf in sorted(_PAST, key=len, reverse=True):
        if phrase == inf or phrase.startswith(inf + " "):
            return _PAST[inf] + phrase[len(inf):]
    return phrase


# endpoint -> callable returning an infinitive verb phrase (subject = the user):
# success logs "<user> <past tense>", failure "<user> failed to <phrase>".
_PHRASES = {
    "auth.logout": lambda: "log out",
    "vms.create_vm": lambda: f"create VM {_body().get('name', '?')}"
    + (f" on {_lookup('nodes', 'hostname', _body().get('node_id'))}" if _body().get("node_id") else ""),
    "vms.start_vm": lambda: f"start VM {_vm()}{_on_node()}",
    "vms.stop_vm": _stop,
    "vms.reboot_vm": lambda: f"reboot VM {_vm()}{_on_node()}",
    "vms.suspend_vm": lambda: f"suspend VM {_vm()}{_on_node()}",
    "vms.resume_vm": lambda: f"resume VM {_vm()}{_on_node()}",
    "vms.delete_vm": lambda: f"delete VM {_vm()}{_on_node()}",
    "vms.migrate_vm": _migrate,
    "vms.update_vm": lambda: f"edit VM {_vm()}{_fields()}",
    "vms.set_vm_xml_route": lambda: f"edit the libvirt XML of VM {_vm()}",
    "vms.request_console": lambda: f"open the console of VM {_vm()}{_on_node()}",
    "vms.renew_vm": lambda: f"renew VM {_vm()}",
    "vms.exempt_vm": lambda: ("exempt VM " if _body().get("exempt", True) else "remove the exemption on VM ")
    + _vm() + " from the lifecycle TTL",
    "vms.set_vm_priority": lambda: f"set the HA priority of VM {_vm()} to {_body().get('priority')}",
    "vms.bulk_set_vm_tenant": lambda: f"move {len(_body().get('vm_ids') or [])} VM(s) to tenant "
    f"{_tenant_name(_body().get('tenant_id'))}",
    "vms.set_lifecycle_settings": lambda: f"set VM lifecycle enforcement to {_body().get('enabled')}",
    "vms.attach_vm_disk": lambda: f"attach a disk to VM {_vm()}",
    "vms.detach_vm_disk": lambda: f"detach disk {(request.view_args or {}).get('target')} from VM {_vm()}",
    "vms.set_vm_cdrom": lambda: f"change the CD-ROM of VM {_vm()}",
    "admin.update_ldap_config": lambda: f"change LDAP settings{_fields()}",
    "admin.test_ldap_config": lambda: "test the LDAP settings",
    "ha.set_runtime": lambda: f"set the HA kill-switch to {'ENABLED' if _body().get('enabled') else 'DISABLED'}",
    "nodes.add_node": lambda: f"add node {_body().get('hostname', '?')}",
    "nodes.delete_node": lambda: f"delete node {_node_arg()}",
    "nodes.update_node": lambda: f"edit node {_node_arg()}{_fields()}",
    "nodes.set_node_tenants": lambda: f"change the tenants of node {_node_arg()}",
    "nodes.set_node_schedulable": lambda: f"set node {_node_arg()} schedulable={_body().get('schedulable')}",
    "nodes.enter_maintenance": lambda: f"put node {_node_arg()} into maintenance",
    "nodes.failover_node": lambda: f"trigger HA failover of node {_node_arg()}",
    "nodes.fence_node": lambda: f"fence node {_node_arg()}",
    "nodes.readmit_node": lambda: f"readmit node {_node_arg()}",
    "tenants.create_tenant": lambda: f"create tenant {_body().get('name', '?')}",
    "tenants.update_tenant": lambda: f"edit tenant {_tenant_name((request.view_args or {}).get('tenant_id'))}{_fields()}",
    "tenants.delete_tenant": lambda: f"delete tenant {getattr(g, '_wvc_tenant', '?')}",
    "users.create_user": lambda: f"create user {_body().get('username', '?')}",
    "users.update_user": lambda: f"edit user {username_for((request.view_args or {}).get('user_id'))}{_fields()}",
    "users.delete_user": lambda: f"delete user {getattr(g, '_wvc_target_user', '?')}",
}

# Quiet non-GET endpoints (machine traffic / token plumbing).
_QUIET = frozenset({"auth.login", "auth.refresh", "agent.heartbeat", "agent.event"})


def _error_text(response) -> str:
    try:
        data = response.get_json(silent=True) or {}
        err = str(data.get("error") or data.get("message") or "").strip()
    except Exception:
        err = ""
    err = err.replace("\n", " ")[:200]
    return f": {err} (HTTP {response.status_code})" if err else f" (HTTP {response.status_code})"


# --------------------------------------------------------------------------- hooks
def install(app) -> None:
    @app.before_request
    def _ctx_start():
        g._wvc_t0 = time.perf_counter()
        request_id()
        if request.method == "GET":
            return
        args = request.view_args or {}
        # Capture names before the action (delete makes them unresolvable).
        if "vm_id" in args:
            g._wvc_vm = _lookup("vms", "name", args["vm_id"])
            try:
                from .services.tenant_service import _get_db

                conn = _get_db()
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT n.hostname AS h FROM vms v JOIN nodes n ON n.id = v.node_id "
                                    "WHERE v.id = %s", (int(args["vm_id"]),))
                        row = cur.fetchone()
                        g._wvc_vm_node = row["h"] if row else None
                finally:
                    conn.close()
            except Exception:
                g._wvc_vm_node = None
        if request.endpoint == "tenants.delete_tenant":
            g._wvc_tenant = _tenant_name(args.get("tenant_id"))
        if request.endpoint == "users.delete_user":
            g._wvc_target_user = username_for(args.get("user_id"))

    @app.after_request
    def _ctx_end(response):
        rid = request_id()
        response.headers["X-Request-ID"] = rid
        t0 = getattr(g, "_wvc_t0", None)
        if t0 is None or (request.path in SILENT_PATHS and response.status_code < 400):
            return response
        try:
            user, uid = current_user()
            # Only for the UI nginx /api/ proxy (it logs + strips the header).
            # Traefik routes /api straight here, so never send it on that path.
            if user != "-" and request.headers.get("X-WVC-Via") == "ui-nginx" \
                    and _trusted(request.remote_addr or ""):
                response.headers[USER_HEADER] = user
            ms = (time.perf_counter() - t0) * 1000.0
            ep = request.endpoint or "-"
            ok = response.status_code < 400
            who = user if user != "-" else "unauthenticated client"
            phrase = _PHRASES.get(ep)
            if phrase is not None:
                try:
                    what = phrase()
                except Exception:
                    what = f"{request.method} {request.path}"
                if ok:
                    event("%s %s", who, _past(what))
                else:
                    event("%s failed to %s", who, what, detail=_error_text(response), level=logging.WARNING)
            elif request.method not in ("GET", "HEAD", "OPTIONS") and ep not in _QUIET:
                event("%s %s %s -> HTTP %s", who, request.method, request.path, response.status_code,
                      level=logging.INFO if ok else logging.WARNING)
            elif response.status_code >= 500:
                event("%s %s %s failed", who, request.method, request.path,
                      detail=_error_text(response), level=logging.WARNING)
            access.debug("%s %s %s %s %.0fms user=%s ip=%s [%s]", request.method, request.path,
                         response.status_code, response.content_length or 0, ms, user, client_ip(), rid)
        except Exception:
            logger.exception("event logging failed for %s %s", request.method, request.path)
        return response
