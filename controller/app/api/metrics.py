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

"""Prometheus scrape endpoint.

Exposed at `GET /metrics` (root path — NOT under /api/). Prometheus
convention is a root-level metrics endpoint; deviating from the /api/
prefix of every other blueprint is intentional.

**Authentication:** none. Protect at the network layer — scrapers should
live in a monitoring subnet that reaches the controller pods only.
Kubernetes ingress rules or NetworkPolicy should keep /metrics off the
public path when this is deployed via Helm.
"""

from flask import Blueprint, Response

from ..metrics import render_metrics

bp = Blueprint("metrics", __name__)


@bp.route("/metrics", methods=["GET"])
def metrics():
    body, content_type = render_metrics()
    return Response(body, mimetype=content_type)
