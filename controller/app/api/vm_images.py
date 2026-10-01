"""VM image catalog endpoints — used by the Quick Create modal.

For v0.14.0 the catalog is hardcoded (see `services.vm_image_service`),
so this blueprint is essentially a thin proxy. When the catalog moves
to a DB table + admin CRUD UI, the API surface stays the same.
"""

from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required

from ..services import vm_image_service

bp = Blueprint("vm_images", __name__)


@bp.route("", methods=["GET"])
@jwt_required()
def list_vm_images():
    """Return the curated golden-image catalog.

    Any authenticated user can read this — knowing which OSes are
    available isn't sensitive, and the actual create RBAC happens on
    `POST /api/vms`.
    """
    return jsonify({"images": vm_image_service.list_images()}), 200
