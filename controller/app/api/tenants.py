"""Tenant management endpoints.

Admin-only CRUD over `tenants`. Tenants represent multi-tenancy units
(typically bound to an AD group DN). See `services/tenant_service.py`.
"""

from flask import Blueprint, jsonify, request

from ..middleware.auth import admin_required
from ..middleware.mcp import mcp_or_jwt_required, current_user_id
from ..services import tenant_service
from ..services.tenant_service import (
    SHARED_TENANT_ID,
    PERSONAL_TENANT_ID,
    get_user_tenant_ids,
    get_user_visibility,
)

bp = Blueprint("tenants", __name__)


@bp.route("", methods=["GET"])
@mcp_or_jwt_required
def list_tenants():
    """List tenants the caller may use as a VM owner.

    Admins get every row. Non-admins get the built-in SHARED (id=1) and
    PERSONAL (id=3) tenants plus any tenants they are a member of via
    `user_tenants` (AD-group mapped at login). This mirrors the same
    visibility that VM operations enforce, so the tenant selector never
    shows tenants a user cannot actually use.

    Also callable by the MCP sidecar via shared-secret + X-Impersonate-User.
    """
    user_id = current_user_id()
    vis     = get_user_visibility(user_id)
    tenants = tenant_service.list_tenants()
    if not vis["is_admin"]:
        allowed = set(get_user_tenant_ids(user_id))
        allowed.update({SHARED_TENANT_ID, PERSONAL_TENANT_ID})
        tenants = [t for t in tenants if t["id"] in allowed]
    return jsonify({"tenants": tenants}), 200


@bp.route("/<int:tenant_id>", methods=["GET"])
@mcp_or_jwt_required
def get_tenant(tenant_id: int):
    """Get a single tenant by id (visibility-filtered for non-admins)."""
    user_id = current_user_id()
    vis     = get_user_visibility(user_id)
    t = tenant_service.get_tenant(tenant_id)
    if not t:
        return jsonify({"error": "Tenant not found"}), 404
    if not vis["is_admin"]:
        allowed = set(get_user_tenant_ids(user_id))
        allowed.update({SHARED_TENANT_ID, PERSONAL_TENANT_ID})
        if tenant_id not in allowed:
            # 404 (not 403) to avoid leaking existence of admin-only tenants.
            return jsonify({"error": "Tenant not found"}), 404
    return jsonify(t), 200


@bp.route("", methods=["POST"])
@admin_required
def create_tenant():
    """Create a new tenant (admin only). Body: {name, ad_group_dn, description?}."""
    body = request.get_json(silent=True) or {}
    result = tenant_service.create_tenant(
        name        = body.get("name"),
        ad_group_dn = body.get("ad_group_dn"),
        description = body.get("description"),
    )
    return jsonify(result), 201 if "ok" in result else 400


@bp.route("/<int:tenant_id>", methods=["PUT"])
@admin_required
def update_tenant(tenant_id: int):
    """Update a tenant (admin only). Built-in tenants reject."""
    body = request.get_json(silent=True) or {}
    if not body:
        return jsonify({"error": "Request body required"}), 400
    result = tenant_service.update_tenant(tenant_id, body)
    status = 200 if "ok" in result else (404 if result.get("error") == "Tenant not found" else 400)
    return jsonify(result), status


@bp.route("/<int:tenant_id>", methods=["DELETE"])
@admin_required
def delete_tenant(tenant_id: int):
    """Delete a tenant (admin only). Rejects built-in and in-use tenants."""
    result = tenant_service.delete_tenant(tenant_id)
    status = 200 if "ok" in result else (404 if result.get("error") == "Tenant not found" else 400)
    return jsonify(result), status
