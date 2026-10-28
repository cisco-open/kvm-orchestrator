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

"""Authentication endpoints."""

from flask import Blueprint, jsonify, request
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    get_jwt_identity,
    jwt_required,
)

from ..services import auth_service
from .. import metrics

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["POST"])
def login():
    """Authenticate user and return JWT tokens."""
    data = request.get_json()
    username = data.get("username")
    password = data.get("password")

    if not username or not password:
        metrics.logins_total.labels(result="missing_fields").inc()
        return jsonify({"error": "Username and password required"}), 400

    user = auth_service.authenticate(username, password)
    if not user:
        metrics.logins_total.labels(result="invalid_credentials").inc()
        return jsonify({"error": "Invalid username or password"}), 401

    tokens = auth_service.issue_tokens(user["id"])
    metrics.logins_total.labels(result="success").inc()
    return jsonify(tokens), 200


@bp.route("/refresh", methods=["POST"])
@jwt_required(refresh=True)
def refresh():
    """Refresh an access token."""
    identity = get_jwt_identity()
    access_token = create_access_token(identity=identity)
    return jsonify({"access_token": access_token}), 200


@bp.route("/me", methods=["GET"])
@jwt_required()
def me():
    """Return current user info plus tenant memberships.

    v0.11+ additions:
      * `display_name` — for the sidebar identity strip (falls back to
        `username` in the UI if NULL).
      * `tenants[]`    — the AD-group-mapped tenants the user is a member
        of. SHARED/PERSONAL are excluded because every user implicitly
        "belongs" to them; the UI knows about both built-in ids by
        constant. Used to populate the tenant selector in the Create
        modal (and to relax the Edit modal's tenant filter) for
        non-admins.
    """
    import os, pymysql, pymysql.cursors
    from urllib.parse import urlparse
    user_id = get_jwt_identity()
    url = urlparse(os.environ.get("DATABASE_URL", ""))
    conn = pymysql.connect(host=url.hostname, port=url.port or 3306,
                           user=url.username, password=url.password,
                           database=url.path.lstrip("/"),
                           cursorclass=pymysql.cursors.DictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, display_name, email, is_admin "
                "FROM users WHERE id = %s",
                (user_id,),
            )
            row = cur.fetchone()
            if not row:
                return jsonify({"error": "User not found"}), 404
            cur.execute(
                """
                SELECT t.id, t.name, t.ad_group_dn, t.is_builtin
                FROM user_tenants ut
                JOIN tenants t ON t.id = ut.tenant_id
                WHERE ut.user_id = %s
                ORDER BY t.name
                """,
                (user_id,),
            )
            tenant_rows = cur.fetchall()
    finally:
        conn.close()
    tenants = [
        {
            "id":           t["id"],
            "name":         t["name"],
            "ad_group_dn":  t["ad_group_dn"],
            "is_builtin":   bool(t["is_builtin"]),
        }
        for t in tenant_rows
    ]
    return jsonify({
        "id":           row["id"],
        "username":     row["username"],
        "display_name": row["display_name"],
        "email":        row["email"],
        "is_admin":     bool(row["is_admin"]),
        "tenants":      tenants,
    }), 200


@bp.route("/logout", methods=["POST"])
@jwt_required()
def logout():
    """Logout (client discards token; optionally blocklist it)."""
    # TODO: Add token to blocklist in Redis
    return jsonify({"message": "Logged out"}), 200
