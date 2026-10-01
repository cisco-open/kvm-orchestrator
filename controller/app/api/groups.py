"""Group management endpoints."""

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

bp = Blueprint("groups", __name__)


@bp.route("", methods=["GET"])
@jwt_required()
def list_groups():
    """List groups (user sees own groups; admin sees all)."""
    return jsonify({"groups": []}), 200


@bp.route("", methods=["POST"])
@jwt_required()
def create_group():
    """Create a group (admin only)."""
    data = request.get_json()
    return jsonify({"error": "Not implemented"}), 501


@bp.route("/<int:group_id>", methods=["GET"])
@jwt_required()
def get_group(group_id: int):
    """Get group detail + member list."""
    return jsonify({"error": "Not implemented"}), 501


@bp.route("/<int:group_id>", methods=["PUT"])
@jwt_required()
def update_group(group_id: int):
    """Update group (name, AD DN mapping)."""
    data = request.get_json()
    return jsonify({"error": "Not implemented"}), 501


@bp.route("/<int:group_id>", methods=["DELETE"])
@jwt_required()
def delete_group(group_id: int):
    """Delete a group (admin only)."""
    return jsonify({"error": "Not implemented"}), 501


@bp.route("/<int:group_id>/members", methods=["GET"])
@jwt_required()
def list_members(group_id: int):
    """List group members."""
    return jsonify({"members": []}), 200


@bp.route("/<int:group_id>/members", methods=["POST"])
@jwt_required()
def add_member(group_id: int):
    """Add a member to the group."""
    data = request.get_json()
    return jsonify({"error": "Not implemented"}), 501


@bp.route("/<int:group_id>/members/<int:user_id>", methods=["DELETE"])
@jwt_required()
def remove_member(group_id: int, user_id: int):
    """Remove a member from the group."""
    return jsonify({"error": "Not implemented"}), 501


@bp.route("/<int:group_id>/vms", methods=["GET"])
@jwt_required()
def list_group_vms(group_id: int):
    """List VMs in group."""
    return jsonify({"vms": []}), 200


@bp.route("/sync", methods=["POST"])
@jwt_required()
def trigger_sync():
    """Trigger AD group sync (admin only)."""
    return jsonify({"error": "Not implemented"}), 501
