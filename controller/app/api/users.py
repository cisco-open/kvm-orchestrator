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

"""User management endpoints.

`list_users` returns a roster used by the VM Edit modal's Owner dropdown,
which is admin-only because non-admins shouldn't be able to enumerate
every account in the system. The create/update/delete endpoints are
still stubs — local-account CRUD is not in the v0.10 scope (all users
arrive via AD on first login through `auth.py`).

v0.14.2 — adds `GET /api/users/me/ssh-pubkeys` so the Quick Create
modal can pre-fill the SSH key textarea with the caller's saved keys
(most-recently-used first). Keys are auto-saved as a side-effect of
every successful Linux Quick Create; there is no separate POST.
"""

import logging
import os
from datetime import datetime
from urllib.parse import urlparse

import pymysql
import pymysql.cursors
from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from ..middleware.auth import admin_required
from ..middleware.mcp import mcp_or_jwt_required, current_user_id
from ..services import ssh_pubkey_service

logger = logging.getLogger("wvc.api.users")

bp = Blueprint("users", __name__)


def _get_db():
    url = urlparse(os.environ.get("DATABASE_URL", ""))
    return pymysql.connect(
        host=url.hostname, port=url.port or 3306,
        user=url.username, password=url.password,
        database=url.path.lstrip("/"),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


@bp.route("", methods=["GET"])
@admin_required
def list_users():
    """Return all known users (active and disabled).

    Used by the VM Edit modal's Owner dropdown. Admin-only — non-admins
    have no business enumerating the full user list.
    """
    conn = _get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, username, email, display_name, auth_source,
                       is_admin, is_active, last_login_at, created_at
                FROM users
                ORDER BY is_active DESC, username
                """
            )
            rows = cur.fetchall()
        for r in rows:
            r["is_admin"]  = bool(r.get("is_admin"))
            r["is_active"] = bool(r.get("is_active"))
            for col in ("last_login_at", "created_at"):
                if isinstance(r.get(col), datetime):
                    r[col] = r[col].isoformat()
        return jsonify({"users": rows}), 200
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# v0.14.2 — self-service SSH pubkey store
# ---------------------------------------------------------------------------

@bp.route("/me/ssh-pubkeys", methods=["GET"])
@jwt_required()
def list_my_ssh_pubkeys():
    """Return the caller's saved SSH public keys, most-recently-used first.

    Consumed by the Quick Create modal — the first row in the list is
    what gets pre-filled into the textarea. Returning `[]` for a new
    user is the normal case (their first Quick Create populates this);
    the modal then just shows the empty textarea as before.

    Response shape:
        {
          "ssh_pubkeys": [
            {"id": 1, "fingerprint": "abc…", "pubkey": "ssh-ed25519 …",
             "comment": "you@laptop", "created_at": "…", "last_used_at": "…"},
            …
          ]
        }
    """
    user_id = int(get_jwt_identity())
    return jsonify({"ssh_pubkeys": ssh_pubkey_service.list_for_user(user_id)}), 200


@bp.route("/me", methods=["GET"])
@mcp_or_jwt_required
def whoami():
    """Return the caller's WVC user record — used by the self-service MCP
    to resolve the impersonated CEC to a numeric ``owner_user_id`` so it
    can count *owned* VMs (not just *visible* VMs, which would include
    every VM in every tenant the caller is a member of).

    Response: ``{"id", "username", "email", "display_name", "is_admin",
    "is_active", "mcp_wvc_enabled"}``.
    """
    uid = current_user_id()
    conn = _get_db()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                "SELECT id, username, email, display_name, "
                "COALESCE(is_admin,0) AS is_admin, "
                "COALESCE(is_active,1) AS is_active, "
                "COALESCE(mcp_wvc_enabled,1) AS mcp_wvc_enabled "
                "FROM users WHERE id = %s",
                (uid,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"error": "User not found"}), 404
    # Normalise tinyint(1) → bool for JSON callers.
    for k in ("is_admin", "is_active", "mcp_wvc_enabled"):
        row[k] = bool(row[k])
    return jsonify(row), 200


@bp.route("", methods=["POST"])
@jwt_required()
def create_user():
    """Create a local user.

    Not implemented — accounts are AD-only in v0.10. Provisioning happens
    on first successful login (see `auth.login`).
    """
    return jsonify({"error": "Not implemented"}), 501


@bp.route("/<int:user_id>", methods=["PUT"])
@jwt_required()
def update_user(user_id: int):
    """Update user details. Not implemented."""
    return jsonify({"error": "Not implemented"}), 501


@bp.route("/<int:user_id>", methods=["DELETE"])
@jwt_required()
def delete_user(user_id: int):
    """Delete a user. Not implemented."""
    return jsonify({"error": "Not implemented"}), 501
