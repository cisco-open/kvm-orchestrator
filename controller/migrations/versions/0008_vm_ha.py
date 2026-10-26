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

"""HA: ha_events table + vms.libvirt_xml

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-24

Two additions needed by the VM-HA engine:

  1. `ha_events` table — audit trail for every HA action (detection,
     fence, per-VM relocation, readmit). The HAEvent model
     (app/models/ha_event.py) has existed since the initial scaffolding
     but no migration ever created the table. Columns mirror the model:
       id, timestamp, node_id (FK nodes), vm_id (FK vms, nullable),
       source_node_id, target_node_id, trigger, result, detail (JSON).
     `detail` records the concrete side-effects (e.g. the ceph blocklist
     entry added) so the readmit/recovery flow knows what to undo.

  2. `vms.libvirt_xml` MEDIUMTEXT — the last-known-good portable domain
     XML, captured on each vm_sync poll. Needed because you cannot
     `virsh dumpxml` a DEAD host at failover time; the scalar vm columns
     aren't enough to rebuild a domain on the target.

Idempotent: guards every DDL with an INFORMATION_SCHEMA check so
`alembic upgrade 0008` can be re-run safely.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(bind, name: str) -> bool:
    row = bind.exec_driver_sql(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_name = %s",
        (name,),
    ).scalar()
    return bool(row)


def _column_exists(bind, table: str, column: str) -> bool:
    row = bind.exec_driver_sql(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s",
        (table, column),
    ).scalar()
    return bool(row)


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "ha_events"):
        op.execute(
            """
            CREATE TABLE `ha_events` (
              `id` int NOT NULL AUTO_INCREMENT,
              `timestamp` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
              `node_id` int DEFAULT NULL,
              `vm_id` int DEFAULT NULL,
              `source_node_id` int DEFAULT NULL,
              `target_node_id` int DEFAULT NULL,
              `trigger` varchar(30) NOT NULL,
              `result` varchar(20) NOT NULL,
              `detail` json DEFAULT NULL,
              PRIMARY KEY (`id`),
              KEY `ix_ha_events_node_id` (`node_id`),
              KEY `ix_ha_events_timestamp` (`timestamp`),
              CONSTRAINT `fk_ha_events_node`
                FOREIGN KEY (`node_id`) REFERENCES `nodes` (`id`) ON DELETE SET NULL,
              CONSTRAINT `fk_ha_events_vm`
                FOREIGN KEY (`vm_id`) REFERENCES `vms` (`id`) ON DELETE SET NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
            """
        )

    if not _column_exists(bind, "vms", "libvirt_xml"):
        op.execute("ALTER TABLE `vms` ADD COLUMN `libvirt_xml` MEDIUMTEXT NULL")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS `ha_events`")
    bind = op.get_bind()
    if _column_exists(bind, "vms", "libvirt_xml"):
        op.execute("ALTER TABLE `vms` DROP COLUMN `libvirt_xml`")
