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

"""add vms.os_family (linux/windows/custom) + best-effort backfill

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-09

Adds an OS classification per VM so metrics can break VM creation/inventory
down by OS (windows / linux / custom).

  * os_family VARCHAR(16) NULL   -- 'linux' | 'windows' | 'custom' | NULL(unknown)

'custom' = created from an existing disk (no catalog image) or a discovered VM
we can't classify. Set on create from the catalog image's os_family.

Best-effort backfill for legacy rows: infer from the VM name / disk image path
(win/windows -> windows; common linux distro markers -> linux). Anything
unmatched is left NULL and shows as 'unknown' in charts.

Idempotent: guards the ADD COLUMN; backfill only touches NULL rows.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {
        row[0]
        for row in bind.exec_driver_sql(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'vms'"
        )
    }
    if "os_family" not in existing:
        op.execute("ALTER TABLE `vms` ADD COLUMN `os_family` VARCHAR(16) NULL")

    # Best-effort backfill (NULL rows only). Order matters: windows first.
    op.execute(
        "UPDATE `vms` SET `os_family` = 'windows' "
        "WHERE `os_family` IS NULL AND ("
        "  LOWER(name) REGEXP 'win|w10|w11|windows|w2k|server20' "
        "  OR LOWER(COALESCE(disk_path,'')) REGEXP 'win|windows')"
    )
    op.execute(
        "UPDATE `vms` SET `os_family` = 'linux' "
        "WHERE `os_family` IS NULL AND ("
        "  LOWER(name) REGEXP 'ubuntu|debian|centos|rhel|rocky|alma|fedora|linux|k8s|kvm|node' "
        "  OR LOWER(COALESCE(disk_path,'')) REGEXP 'ubuntu|debian|centos|rhel|rocky|alma|fedora')"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE `vms` DROP COLUMN `os_family`")
