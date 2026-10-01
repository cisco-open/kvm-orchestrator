"""Role and permission management endpoints."""

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

bp = Blueprint("roles", __name__)


@bp.route("", methods=["GET"])
@jwt_required()
def list_roles():
    """List all roles."""
    return jsonify({"roles": []}), 200


@bp.route("", methods=["POST"])
@jwt_required()
def create_role():
    """Create a custom role (admin only)."""
    data = request.get_json()
    return jsonify({"error": "Not implemented"}), 501


@bp.route("/<int:role_id>/permissions", methods=["PUT"])
@jwt_required()
def update_permissions(role_id: int):
    """Update permissions for a role."""
    data = request.get_json()
    return jsonify({"error": "Not implemented"}), 501
