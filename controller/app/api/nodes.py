"""Node management endpoints."""

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from ..middleware.auth import admin_required
from ..services import node_service
from ..services.node_monitor import get_node_metrics as _get_node_metrics
from ..services.vm_create import (
    get_node_resources as _get_node_resources,
    list_path as _list_path,
    list_pool_volumes as _list_pool_volumes,
)

bp = Blueprint("nodes", __name__)


# ---------------------------------------------------------------------------
# SSH keys
# ---------------------------------------------------------------------------

@bp.route("/ssh-keys", methods=["GET"])
@admin_required
def list_ssh_keys():
    """List stored SSH keys (name + id, no private key material)."""
    keys = node_service.list_ssh_keys()
    return jsonify({"ssh_keys": keys}), 200


@bp.route("/ssh-keys", methods=["POST"])
@admin_required
def add_ssh_key():
    """Store a new named SSH private key."""
    data = request.get_json()
    name = data.get("name", "").strip()
    private_key = data.get("private_key", "").strip()

    if not name or not private_key:
        return jsonify({"error": "name and private_key are required"}), 400

    key = node_service.add_ssh_key(name, private_key)
    return jsonify({"ssh_key": key}), 201


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

@bp.route("", methods=["GET"])
@jwt_required()
def list_nodes():
    """List compute nodes visible to the caller.

    Admins see everything; non-admins see nodes tagged with DEFAULT or
    with any tenant they belong to. Nodes with no tenant tags are
    admin-only.
    """
    user_id = int(get_jwt_identity())
    nodes = node_service.list_nodes(user_id=user_id)
    return jsonify({"nodes": nodes}), 200


@bp.route("", methods=["POST"])
@admin_required
def add_node():
    """Register a new compute node.

    Accepts an optional `tenant_ids: [int]` field to tag the node on
    creation. A node with no tenant tags is only visible to admins.
    """
    data = request.get_json() or {}

    # hostname is optional — resolved from the remote host via SSH if omitted
    hostname  = (data.get("hostname") or "").strip()
    ip_address = data.get("ip_address", "").strip()
    ssh_user   = data.get("ssh_user", "").strip()
    ssh_key_id = data.get("ssh_key_id")

    if not all([ip_address, ssh_user, ssh_key_id]):
        return jsonify({"error": "ip_address, ssh_user, and ssh_key_id are required"}), 400

    ssh_host = data.get("ssh_host", "").strip() or None
    ssh_port = int(data.get("ssh_port", 22))

    tenant_ids = data.get("tenant_ids") or []
    if not isinstance(tenant_ids, list) or not all(isinstance(t, int) for t in tenant_ids):
        return jsonify({"error": "tenant_ids must be a list of integers"}), 400

    try:
        node = node_service.add_node(
            hostname=hostname,
            ip_address=ip_address,
            ssh_user=ssh_user,
            ssh_key_id=ssh_key_id,
            ssh_host=ssh_host,
            ssh_port=ssh_port,
            tenant_ids=tenant_ids,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"node": node}), 201


@bp.route("/<int:node_id>", methods=["GET"])
@jwt_required()
def get_node(node_id: int):
    """Get node details. 404s if the caller can't see the node."""
    user_id = int(get_jwt_identity())
    node = node_service.get_node(node_id, user_id=user_id)
    if not node:
        return jsonify({"error": "Node not found"}), 404
    return jsonify({"node": node}), 200


@bp.route("/<int:node_id>", methods=["DELETE"])
@admin_required
def delete_node(node_id: int):
    """Remove a node."""
    if not node_service.delete_node(node_id):
        return jsonify({"error": "Node not found"}), 404
    return jsonify({"message": "Node deleted"}), 200


@bp.route("/<int:node_id>", methods=["PUT"])
@admin_required
def update_node(node_id: int):
    """Update node configuration."""
    # TODO: implement general node updates (hostname, ssh_*, ha_priority, ...)
    return jsonify({"error": "Not implemented"}), 501


@bp.route("/<int:node_id>/tenants", methods=["PUT"])
@admin_required
def set_node_tenants(node_id: int):
    """Replace the set of tenants a node is tagged with (admin only).

    Body: `{ "tenant_ids": [int, ...] }`. Empty list clears all tags —
    making the node admin-only.
    """
    data = request.get_json() or {}
    tenant_ids = data.get("tenant_ids")
    if not isinstance(tenant_ids, list) or not all(isinstance(t, int) for t in tenant_ids):
        return jsonify({"error": "tenant_ids must be a list of integers"}), 400

    try:
        node = node_service.set_node_tenants(node_id, tenant_ids)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not node:
        return jsonify({"error": "Node not found"}), 404
    return jsonify({"node": node}), 200


@bp.route("/<int:node_id>/schedulable", methods=["POST"])
@admin_required
def set_node_schedulable(node_id: int):
    """Cordon / uncordon a node (admin only).

    Body: `{ "schedulable": bool }`. When false, the node is cordoned:
    placement and the migrate-target UI exclude it, so no new VMs land
    there. Existing VMs stay running and the node remains online.
    """
    data = request.get_json() or {}
    schedulable = data.get("schedulable")
    if not isinstance(schedulable, bool):
        return jsonify({"error": "schedulable must be a boolean"}), 400

    node = node_service.set_node_schedulable(node_id, schedulable)
    if not node:
        return jsonify({"error": "Node not found"}), 404
    return jsonify({"node": node}), 200


@bp.route("/<int:node_id>/maintenance", methods=["POST"])
@admin_required
def enter_maintenance(node_id: int):
    """Enter maintenance mode — evacuate VMs."""
    # TODO: Trigger VM evacuation
    return jsonify({"error": "Not implemented"}), 501


# ---------------------------------------------------------------------------
# HA — admin-forced failover + guarded readmission
# ---------------------------------------------------------------------------

@bp.route("/<int:node_id>/failover", methods=["POST"])
@admin_required
def failover_node(node_id: int):
    """Admin-forced failover: fence this node and relocate its VMs onto
    survivors. Requires HA to be armed (env master-arm + runtime switch);
    the engine re-checks and refuses otherwise. Same fence-first path as
    automatic failover."""
    from ..services import ha_engine
    result = ha_engine.relocate_vms(node_id, trigger="manual")
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result), 200


@bp.route("/<int:node_id>/fence", methods=["POST"])
@admin_required
def fence_node(node_id: int):
    """Admin-initiated manual fence (HA-page 'Fence' button). Body: optional
    {"force": bool} to override the partition guard. Ceph-blocklists the node
    and marks it 'fenced' so its VMs can be recovered. This is the human
    authorisation the engine deliberately does NOT perform automatically."""
    from ..services import ha_engine
    body = request.get_json(silent=True) or {}
    force = bool(body.get("force", False))
    result = ha_engine.admin_fence_node(node_id, force=force)
    if "error" in result:
        code = 409 if result.get("partition_suspected") else 400
        return jsonify(result), code
    return jsonify(result), 200


@bp.route("/<int:node_id>/readmit", methods=["POST"])
@admin_required
def readmit_node(node_id: int):
    """Guarded recovery of a fenced node: destroy stale relocated domains
    on the returning host, verify no split-brain watcher remains, then
    un-blocklist and mark online. Human-gated (admin action)."""
    from ..services import ha_engine
    result = ha_engine.readmit_node(node_id)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result), 200


@bp.route("/<int:node_id>/readmit-check", methods=["GET"])
@admin_required
def readmit_check(node_id: int):
    """READ-ONLY pre-flight — tells the admin whether it is SAFE to readmit
    this fenced node (reachability, stale/running domains, split-brain watcher
    check) with a safe/caution/unsafe verdict. Changes nothing."""
    from ..services import ha_engine
    result = ha_engine.readmit_check(node_id)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result), 200


@bp.route("/<int:node_id>/resources", methods=["GET"])
@jwt_required()
def get_node_resources(node_id: int):
    """Discover available storage pools, networks, and ISOs on a node."""
    result = _get_node_resources(node_id)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result), 200


@bp.route("/<int:node_id>/browse", methods=["GET"])
@admin_required
def browse_node_path(node_id: int):
    """List files and directories at a path on a node."""
    path = request.args.get("path", "/var/lib/libvirt/images")
    result = _list_path(node_id, path)
    return jsonify(result), 200 if "error" not in result else 400


@bp.route("/<int:node_id>/pool-volumes", methods=["GET"])
@jwt_required()
def pool_volumes(node_id: int):
    """List volumes/images in a storage pool on a node (works for all pool types)."""
    pool = request.args.get("pool", "").strip()
    if not pool:
        return jsonify({"error": "pool parameter is required"}), 400
    result = _list_pool_volumes(node_id, pool)
    return jsonify(result), 200 if "error" not in result else 400


@bp.route("/<int:node_id>/metrics", methods=["GET"])
@jwt_required()
def node_metrics(node_id: int):
    """Return CPU% / RAM-used time-series samples for the node.

    Query params:
      window — seconds of history to return (default 3600, max 86400).

    Response: { samples: [ { t, cpu, ram_used_mb, ram_total_mb }, ... ] }
    """
    try:
        window = int(request.args.get("window", "3600"))
    except ValueError:
        window = 3600
    window = max(60, min(window, 86400))
    samples = _get_node_metrics(node_id, window)
    return jsonify({"samples": samples, "window": window}), 200
