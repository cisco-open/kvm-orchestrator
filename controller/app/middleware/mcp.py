"""MCP impersonation middleware — accept requests from the self-service MCP
sidecar and populate a per-request identity as if the impersonated user had
logged in normally.

Trust model
-----------
The MCP sidecar (`self-service/mcp_server.py`) has already authenticated
its own caller via an `ss_<hex>` API key (see `services/mcp_auth.py` in the
external auth sidecar). That key maps to a username stored in the sidecar's
self-service DB. To act on that user's behalf against wvc's REST API — which
otherwise requires a JWT issued by wvc's own /api/auth/login flow after a
successful LDAP bind — the MCP presents two headers on every call:

    Authorization: Bearer <MCP_SHARED_SECRET>
    X-Impersonate-User: <cec>

The shared secret is a random string (>=32 bytes recommended) known only to
the wvc controller pods and the MCP sidecar via a k8s Secret. If it matches,
we resolve the username to a row in `users` and stash the user's id on `flask.g`
so all downstream visibility/admin checks see the impersonated user.

Guards
------
* Missing/mismatched secret → 401.
* Unknown user (never logged in through the normal LDAP flow) → 403.
* User with `is_active=0` → 403.
* User with `mcp_wvc_enabled=0` (opt-out) → 403.

Integration
-----------
The `@mcp_or_jwt_required` decorator falls through to the standard
`verify_jwt_in_request()` path when the MCP secret is absent, so browser +
curl JWT callers keep working unchanged.

Endpoints should read the caller's user id via `current_user_id()` from
this module (not `get_jwt_identity()` directly) so both paths work.
`admin_required` uses the same helper.
"""

import logging
import os
from functools import wraps
from urllib.parse import urlparse

import pymysql
import pymysql.cursors
from flask import g, jsonify, request
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request

from ..metrics import mcp_auth_total

log = logging.getLogger("wvc.mcp_auth")


def _get_db():
    url = urlparse(os.environ.get("DATABASE_URL", ""))
    return pymysql.connect(
        host=url.hostname, port=url.port or 3306,
        user=url.username, password=url.password,
        database=url.path.lstrip("/"),
        cursorclass=pymysql.cursors.DictCursor,
    )


def _extract_mcp_headers() -> tuple[str | None, str | None]:
    """Return (shared_secret, username) from the request headers, or (None, None)
    if this doesn't look like an MCP-originated request.

    We accept the shared secret either as `Authorization: Bearer <secret>`
    OR as an explicit
    `X-MCP-Shared-Secret` header for clarity in curl/debug flows.
    """
    cec = request.headers.get("X-Impersonate-User")
    if not cec:
        return None, None  # no impersonation attempt at all

    secret = request.headers.get("X-MCP-Shared-Secret")
    if not secret:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            secret = auth[7:]
    return secret, cec.strip().lower()


def _resolve_mcp_user(cec: str) -> dict | None:
    """Return the user row for `cec`, or None if not eligible.

    Requires the user to (a) exist, (b) be active, and (c) not have opted
    out of MCP access via `mcp_wvc_enabled=0`.
    """
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, is_admin, is_active, "
                "       COALESCE(mcp_wvc_enabled, 1) AS mcp_wvc_enabled "
                "FROM users WHERE username = %s",
                (cec,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return None
    if not row["is_active"] or not row["mcp_wvc_enabled"]:
        return None
    return row


def mcp_or_jwt_required(fn):
    """Decorator: accept either an MCP-impersonated request or a normal JWT."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        secret, cec = _extract_mcp_headers()
        expected = os.environ.get("MCP_SHARED_SECRET", "")

        if cec is not None:
            # MCP-style request. Validate secret & user.
            if not expected or not secret or secret != expected:
                mcp_auth_total.labels(result="bad_secret").inc()
                log.warning("mcp_auth reject cec=%s reason=bad_secret", cec)
                return jsonify({"error": "Invalid MCP shared secret"}), 401

            user = _resolve_mcp_user(cec)
            if not user:
                mcp_auth_total.labels(result="unknown_or_disabled").inc()
                log.warning("mcp_auth reject cec=%s reason=unknown_or_disabled", cec)
                return jsonify({"error": "MCP user not found or disabled"}), 403

            g.mcp_impersonated_user_id = user["id"]
            g.mcp_impersonated_cec = cec
            g.mcp_impersonated_is_admin = bool(user["is_admin"])
            mcp_auth_total.labels(result="ok").inc()
            log.info("mcp_auth ok cec=%s user_id=%d admin=%s",
                     cec, user["id"], bool(user["is_admin"]))
            return fn(*args, **kwargs)

        # Not an MCP request — fall through to normal JWT verification.
        verify_jwt_in_request()
        return fn(*args, **kwargs)

    return wrapper


def current_user_id() -> int:
    """Return the current caller's user id.

    Prefers the MCP impersonated id set by `mcp_or_jwt_required` if present,
    else falls back to the standard JWT identity. Endpoints should use this
    helper instead of calling `get_jwt_identity()` directly so they work
    under both paths.
    """
    mcp_uid = getattr(g, "mcp_impersonated_user_id", None)
    if mcp_uid is not None:
        return int(mcp_uid)
    return int(get_jwt_identity())


def is_mcp_request() -> bool:
    """Return True iff the current request was authenticated via MCP."""
    return getattr(g, "mcp_impersonated_user_id", None) is not None


def mcp_caller_cec() -> str | None:
    """Return the impersonated CEC for the current request, or None."""
    return getattr(g, "mcp_impersonated_cec", None)
