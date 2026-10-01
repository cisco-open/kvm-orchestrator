"""add vm_lifecycle_events (create/delete) for cumulative VM-count charts

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-09

VMs are hard-deleted, so we can't reconstruct a VM count that rises on create
and FALLS on delete from the `vms` table alone. This append-only ledger records
a row per create/delete so the metrics API can compute a running cumulative
total over time.

  vm_lifecycle_events(
    id BIGINT PK,
    ts DATETIME,
    event VARCHAR(10),        -- 'create' | 'delete'
    os_family VARCHAR(16),    -- linux | windows | custom | unknown
    vm_name VARCHAR(255),
    vm_id INT NULL
  )

Backfill: seed a 'create' row for every EXISTING vm from its created_at +
os_family, so historical creates show on the chart from day one. (Past deletes
are unrecoverable — the cumulative line is accurate going forward and correct in
level today.)

Idempotent.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = {
        row[0]
        for row in bind.exec_driver_sql(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = DATABASE()"
        )
    }
    if "vm_lifecycle_events" not in tables:
        op.execute(
            "CREATE TABLE `vm_lifecycle_events` ("
            "  `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,"
            "  `ts` DATETIME NOT NULL,"
            "  `event` VARCHAR(10) NOT NULL,"
            "  `os_family` VARCHAR(16) NULL,"
            "  `vm_name` VARCHAR(255) NULL,"
            "  `vm_id` INT NULL,"
            "  KEY `ix_vmle_ts` (`ts`),"
            "  KEY `ix_vmle_event` (`event`)"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        )
        # Backfill create events from EXISTING kvmaestro-created VMs only.
        # Discovered VMs (owner_user_id IS NULL) are NOT counted: their
        # created_at is the discovery time (DB default), not a real creation
        # date, so seeding them would produce a false "created today" spike.
        # We only track the lifecycle of VMs kvmaestro actually created.
        op.execute(
            "INSERT INTO `vm_lifecycle_events` (ts, event, os_family, vm_name, vm_id) "
            "SELECT COALESCE(created_at, UTC_TIMESTAMP()), 'create', "
            "       COALESCE(os_family,'unknown'), name, id "
            "FROM `vms` WHERE owner_user_id IS NOT NULL"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS `vm_lifecycle_events`")
