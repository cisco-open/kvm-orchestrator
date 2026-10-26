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

"""Unauthenticated liveness/readiness endpoint for Kubernetes probes.

This is intentionally at /healthz (root, not /api) so ingress + probe
configuration can target it without needing a JWT. The endpoint returns
200 OK if:
  - The process is running (implicit — Flask replied)
  - A cheap DB round-trip succeeds (SELECT 1)

If MySQL is unreachable we return 503 so kubelet will mark the pod
NotReady and take it out of the Service endpoint list until MySQL comes
back. This matches common load-balancer health-probe conventions.
"""

import logging

from flask import Blueprint, jsonify
from sqlalchemy import text

from ..extensions import engine

logger = logging.getLogger("wvc.health")

bp = Blueprint("health", __name__)


@bp.route("/healthz", methods=["GET"])
def healthz():
    """Liveness + readiness probe target.

    Returns 200 with {"status": "ok"} if the app can round-trip a
    trivial query to MySQL. Returns 503 with {"status": "degraded",
    "error": "..."} otherwise.
    """
    if engine is None:
        # create_app hasn't finished; the app shouldn't be serving yet
        # but if it is, tell k8s we're not ready.
        return jsonify({"status": "degraded", "error": "db not initialised"}), 503

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — surface anything to k8s
        logger.warning("healthz DB check failed: %s", exc)
        return jsonify({"status": "degraded", "error": str(exc)}), 503

    return jsonify({"status": "ok"}), 200
