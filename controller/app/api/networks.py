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

"""Networks catalog endpoints (v0.15.0).

GET is open to all authenticated users — non-admins get the user_visible
subset (needed for the Quick Create dropdown). POST/PUT/DELETE are
admin-only.

The blueprint is mounted at `/api/networks` from `api/__init__.py`.
"""

from flask import Blueprint, jsonify, request

from ..middleware.auth import admin_required
from ..middleware.mcp import mcp_or_jwt_required, current_user_id
from ..services import network_service
from ..services.tenant_service import get_user_visibility

bp = Blueprint("networks", __name__)


@bp.route("", methods=["GET"])
@mcp_or_jwt_required
def list_networks():
    """List networks.

    Admins see every row; non-admins see only user_visible=1. The shape
    is unchanged either way so the frontend can use a single client
    function — what changes is only the filter applied here.

    Also callable by the MCP sidecar via shared-secret + X-Impersonate-User.
    """
    user_id = current_user_id()
    vis     = get_user_visibility(user_id)
    rows = network_service.list_networks(visible_only=not vis["is_admin"])
    return jsonify({"networks": rows}), 200


@bp.route("/<int:network_id>", methods=["GET"])
@mcp_or_jwt_required
def get_network(network_id: int):
    """Get a single network by id (visibility-filtered for non-admins)."""
    user_id = current_user_id()
    vis     = get_user_visibility(user_id)
    n = network_service.get_network(network_id)
    if not n:
        return jsonify({"error": "Network not found"}), 404
    if not vis["is_admin"] and not n["user_visible"]:
        # Same 404-not-403 pattern used by VM visibility — don't leak the
        # existence of admin-only networks.
        return jsonify({"error": "Network not found"}), 404
    return jsonify(n), 200


@bp.route("", methods=["POST"])
@admin_required
def create_network():
    """Create a network (admin only).

    Body: {name, bridge_name, vlan_id?, description?, user_visible?, is_default?}
    """
    body = request.get_json(silent=True) or {}
    result = network_service.create_network(
        name         = body.get("name"),
        bridge_name  = body.get("bridge_name"),
        vlan_id      = body.get("vlan_id"),
        description  = body.get("description"),
        user_visible = bool(body.get("user_visible")),
        is_default   = bool(body.get("is_default")),
    )
    return jsonify(result), 201 if "ok" in result else 400


@bp.route("/<int:network_id>", methods=["PUT"])
@admin_required
def update_network(network_id: int):
    """Update a network (admin only). Any subset of the create fields."""
    body = request.get_json(silent=True) or {}
    if not body:
        return jsonify({"error": "Request body required"}), 400
    result = network_service.update_network(network_id, body)
    status = (200 if "ok" in result
              else 404 if result.get("error") == "Network not found"
              else 400)
    return jsonify(result), status


@bp.route("/<int:network_id>", methods=["DELETE"])
@admin_required
def delete_network(network_id: int):
    """Delete a network (admin only). Rejects if any VM still uses it."""
    result = network_service.delete_network(network_id)
    status = (200 if "ok" in result
              else 404 if result.get("error") == "Network not found"
              else 400)
    return jsonify(result), status
