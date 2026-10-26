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
