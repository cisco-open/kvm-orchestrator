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

"""add nodes.schedulable cordon flag

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-20

Adds a single column to the `nodes` table:

  * schedulable TINYINT(1) NOT NULL DEFAULT 1

When 0, the node is "cordoned": the placement algorithm
(`app/services/placement.py`) and the migrate-target UI exclude it, so no
new VMs (create or migrate) will land there. Existing VMs on the node are
untouched — it stays online for the guests it already hosts. Admin-only
toggle via `POST /api/nodes/<id>/schedulable`.

Idempotent: uses INFORMATION_SCHEMA to guard the ADD COLUMN so
`alembic upgrade 0007` can be re-run.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {
        row[0]
        for row in bind.exec_driver_sql(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'nodes'"
        )
    }
    if "schedulable" not in existing:
        op.execute(
            "ALTER TABLE `nodes` "
            "ADD COLUMN `schedulable` TINYINT(1) NOT NULL DEFAULT 1"
        )


def downgrade() -> None:
    op.execute("ALTER TABLE `nodes` DROP COLUMN `schedulable`")
