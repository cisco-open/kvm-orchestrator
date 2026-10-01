"""Audit log endpoints."""

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

bp = Blueprint("audit", __name__)


@bp.route("", methods=["GET"])
@jwt_required()
def query_audit_log():
    """Query audit log (admin only)."""
    # TODO: Filter by action, user, resource, date range
    return jsonify({"events": []}), 200
