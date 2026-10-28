# SPDX-License-Identifier: Apache-2.0
#
# Copyright 2026 Cisco Systems, Inc. and its affiliates
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
