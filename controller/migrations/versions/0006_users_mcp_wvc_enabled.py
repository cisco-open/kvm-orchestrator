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

"""add users.mcp_wvc_enabled opt-out flag

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-21

Adds a single nullable-with-default column to the `users` table:

  * mcp_wvc_enabled TINYINT(1) NOT NULL DEFAULT 1

When 0, the MCP impersonation middleware (`middleware/mcp.py`) refuses to
act as that user even with a valid shared secret. Default 1 means opt-in
by default — matches the "if you're a wvc user, you can use the MCP" model
already gated by the caller having minted an `ss_<hex>` key in self-service.
Users or admins can flip this to 0 as an explicit MCP opt-out.

Idempotent: uses INFORMATION_SCHEMA to guard the ADD COLUMN so
`alembic upgrade 0006` can be re-run.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {
        row[0]
        for row in bind.exec_driver_sql(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'users'"
        )
    }
    if "mcp_wvc_enabled" not in existing:
        op.execute(
            "ALTER TABLE `users` "
            "ADD COLUMN `mcp_wvc_enabled` TINYINT(1) NOT NULL DEFAULT 1"
        )


def downgrade() -> None:
    op.execute("ALTER TABLE `users` DROP COLUMN `mcp_wvc_enabled`")
