"""JWT authentication middleware.

MCP-aware: `admin_required` uses `mcp_or_jwt_required` under the hood so
requests authenticated via the MCP impersonation header path (see
`middleware/mcp.py`) also flow through the admin gate correctly.
"""

import os
from functools import wraps
from urllib.parse import urlparse

import pymysql
import pymysql.cursors
from flask import jsonify

from .mcp import mcp_or_jwt_required, current_user_id


def _get_db():
    url = urlparse(os.environ.get("DATABASE_URL", ""))
    return pymysql.connect(
        host=url.hostname,
        port=url.port or 3306,
        user=url.username,
        password=url.password,
        database=url.path.lstrip("/"),
        cursorclass=pymysql.cursors.DictCursor,
    )


def admin_required(fn):
    """Decorator: require the current user to be an admin.

    Accepts either a JWT-authenticated request or an MCP-impersonated one
    (via mcp_or_jwt_required).
    """

    @wraps(fn)
    @mcp_or_jwt_required
    def wrapper(*args, **kwargs):
        user_id = current_user_id()

        conn = _get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT is_admin FROM users WHERE id = %s AND is_active = 1",
                    (user_id,),
                )
                row = cur.fetchone()
        finally:
            conn.close()

        if not row or not row["is_admin"]:
            return jsonify({"error": "Admin access required"}), 403

        return fn(*args, **kwargs)

    return wrapper
