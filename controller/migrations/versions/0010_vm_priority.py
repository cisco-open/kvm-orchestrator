"""add vms.priority HA class (critical/priority/default)

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-04

Adds the HA priority CLASS used by the recovery engine's restart ordering and
capacity-reclamation logic:

  * priority ENUM('critical','priority','default') NOT NULL DEFAULT 'default'

Semantics (admin-set only; enforced at the API layer):

  * critical  — most important. Restarted FIRST on failover. May evict running
                non-critical VMs to reclaim capacity: DEFAULT VMs first, and
                PRIORITY VMs only as a last resort (should not normally happen).
  * priority  — important. Restarted after critical. May evict DEFAULT VMs to
                reclaim capacity. Never evicts critical or other priority VMs.
  * default   — normal. Restarted last, best-effort. Evicts nothing; is itself
                the first candidate for eviction.

Eviction (reclamation) is always a SEPARATE admin approval in the recovery
plan; this column only classifies the VM.

Idempotent: guards the ADD COLUMN via INFORMATION_SCHEMA so it can be re-run.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0010"
down_revision: Union[str, None] = "0009"
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
    if "priority" not in existing:
        op.execute(
            "ALTER TABLE `vms` "
            "ADD COLUMN `priority` ENUM('critical','priority','default') "
            "NOT NULL DEFAULT 'default'"
        )


def downgrade() -> None:
    op.execute("ALTER TABLE `vms` DROP COLUMN `priority`")
