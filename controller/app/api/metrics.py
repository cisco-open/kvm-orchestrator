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
