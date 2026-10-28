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

"""widen vm_lifecycle_events.event to fit TTL event names

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-14

The 0013 ledger sized `event` as VARCHAR(10) for just 'create'/'delete'. The
v0.30 lifecycle work introduced longer event names — 'auto_delete' (11),
'warn:poststop' (13), 'warn:pre1', 'unexempt', etc. Under STRICT_TRANS_TABLES
the over-length inserts were REJECTED and silently swallowed by the best-effort
recorder, so those audit/dedup events never landed. Widen to VARCHAR(32).

Idempotent.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    row = bind.exec_driver_sql(
        "SELECT character_maximum_length FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = 'vm_lifecycle_events' "
        "AND column_name = 'event'"
    ).fetchone()
    # Widen only if it exists and is narrower than 32.
    if row and (row[0] or 0) < 32:
        op.execute(
            "ALTER TABLE `vm_lifecycle_events` "
            "MODIFY COLUMN `event` VARCHAR(32) NOT NULL"
        )


def downgrade() -> None:
    # No-op: narrowing could truncate existing rows; leave the wider column.
    pass
