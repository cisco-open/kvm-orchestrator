"""Agent-facing endpoints (authenticated by agent key)."""

from flask import Blueprint, jsonify, request

bp = Blueprint("agent", __name__)


def _verify_agent_key():
    """Verify the agent shared key from the request header."""
    from flask import current_app

    key = request.headers.get("X-Agent-Key")
    if not key or key != current_app.config["AGENT_SHARED_KEY"]:
        return False
    return True


@bp.before_request
def check_agent_auth():
    """Require valid agent key for all agent endpoints."""
    if not _verify_agent_key():
        return jsonify({"error": "Invalid agent key"}), 401


@bp.route("/heartbeat", methods=["POST"])
def heartbeat():
    """Receive heartbeat from a node agent and update node status + metrics."""
    data = request.get_json(silent=True) or {}

    hostname = data.get("hostname")
    ip_address = data.get("ip_address")

    if not hostname and not ip_address:
        return jsonify({"error": "hostname or ip_address required"}), 400

    # Build metrics dict from agent payload
    metrics = {
        "status": "online",
        "total_vcpus":      int(data.get("cpu_count", 0)),
        "available_vcpus":  int(data.get("cpu_count", 0)),
        "total_ram_mb":     int(data.get("ram_total_mb", 0)),
        "available_ram_mb": int(data.get("ram_available_mb", 0)),
        "total_disk_gb":    int(data.get("disk_total_gb", 0)),
        "available_disk_gb": int(data.get("disk_used_gb", 0)),  # agent sends used, not avail
    }

    # Look up node by hostname or IP and update
    import pymysql, pymysql.cursors, os
    from urllib.parse import urlparse
    from datetime import datetime

    url = urlparse(os.environ.get("DATABASE_URL", ""))
    conn = pymysql.connect(
        host=url.hostname, port=url.port or 3306,
        user=url.username, password=url.password,
        database=url.path.lstrip("/"),
        cursorclass=pymysql.cursors.DictCursor, autocommit=False,
    )
    try:
        with conn.cursor() as cur:
            # Match on FQDN or short hostname (agents send socket.gethostname(),
            # which is often the short name while the DB stores the FQDN), or on
            # IP. The SUBSTRING_INDEX trims the DB FQDN to its short form so a
            # short-name heartbeat still resolves.
            short = (hostname or "").split(".")[0]
            cur.execute(
                "SELECT id FROM nodes "
                "WHERE hostname=%s "
                "   OR SUBSTRING_INDEX(hostname, '.', 1)=%s "
                "   OR ip_address=%s "
                "LIMIT 1",
                (hostname, short, ip_address),
            )
            row = cur.fetchone()
            if not row:
                return jsonify({"error": "node not registered"}), 404

            from ..services.node_monitor import update_node_status
            update_node_status(row["id"], metrics)
    finally:
        conn.close()

    return jsonify({"status": "ok"}), 200


@bp.route("/register", methods=["POST"])
def register():
    """Register a new node agent."""
    data = request.get_json()
    # TODO: Create or update node record
    return jsonify({"error": "Not implemented"}), 501


@bp.route("/event", methods=["POST"])
def event():
    """Receive agent-initiated events (VM crashed, etc.)."""
    data = request.get_json()
    # TODO: Process event, update VM state, create audit log
    return jsonify({"status": "received"}), 200
