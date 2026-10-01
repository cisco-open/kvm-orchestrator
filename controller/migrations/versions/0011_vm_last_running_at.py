"""add vms.last_running_at (HA: preserve pre-outage running state)

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-08

vm_sync sets a VM to 'unknown' when its host becomes unpollable (we can no
longer trust the last-known state). But HA failover must still relocate VMs
that WERE running before the host vanished — and must NOT power on VMs that
were deliberately shut down. A now-'unknown' state alone can't distinguish the
two.

This column records the last time vm_sync observed the VM in a running/paused
state. HA treats an 'unknown' VM as relocatable only if it was running
recently (last_running_at within a freshness window). Deliberately-stopped
VMs have a stale/NULL last_running_at and are left down.

Idempotent.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0011"
down_revision: Union[str, None] = "0010"
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
    if "last_running_at" not in existing:
        op.execute("ALTER TABLE `vms` ADD COLUMN `last_running_at` DATETIME NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE `vms` DROP COLUMN `last_running_at`")
