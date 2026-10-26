"""VM lifecycle: 1-year TTL — expires_at / renewed_at / exempt / lifecycle_state

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-14

Adds an age-based lifecycle to the `vms` table so kvm-orchestrator can enforce a
1-year TTL (formerly the self-service portal's `vm_lifecycle` table, now retired
and owned here where the VMs, owners, delete path and event ledger already live).

New columns on `vms`:
  expires_at      DATETIME NULL  -- when the VM's current term ends
  renewed_at      DATETIME NULL  -- last renewal ("touch"); NULL = never renewed
  exempt          TINYINT(1) NOT NULL DEFAULT 0  -- admin-only: never expire
  lifecycle_state VARCHAR(16) NOT NULL DEFAULT 'active'
                  -- active | expiring | auto_stopped | (deleted removes the row)

Grace machine (enforced by the daily beat task app.tasks.vm_expiry):
  active   -> warnings begin at ~11 months, escalate approaching expires_at
  expiring -> past expires_at, still warning
  auto_stopped -> VM force-stopped at expiry+grace (reversible; a renew restarts)
  auto-delete  -> after a further grace with no renew, real vm_delete runs.

Backfill:
  * expires_at = created_at + 1 year for every VM that doesn't have one.
  * exempt = 1 for DISCOVERED infra VMs (owner_user_id IS NULL). Their
    created_at is discovery time, not a real creation date, and they are
    typically long-lived lab infrastructure — auto-deleting them would be
    catastrophic. Admins can later un-exempt individual infra VMs in the UI.
    User/portal-created VMs (owner_user_id set) are NOT exempt and follow the
    normal TTL.

Idempotent — safe to re-run; only adds columns/backfills where missing.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(bind, table: str) -> set:
    return {
        row[0]
        for row in bind.exec_driver_sql(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = %s",
            (table,),
        )
    }


def upgrade() -> None:
    bind = op.get_bind()
    cols = _columns(bind, "vms")

    if "expires_at" not in cols:
        op.execute("ALTER TABLE `vms` ADD COLUMN `expires_at` DATETIME NULL")
    if "renewed_at" not in cols:
        op.execute("ALTER TABLE `vms` ADD COLUMN `renewed_at` DATETIME NULL")
    if "exempt" not in cols:
        op.execute(
            "ALTER TABLE `vms` ADD COLUMN `exempt` TINYINT(1) NOT NULL DEFAULT 0"
        )
    if "lifecycle_state" not in cols:
        op.execute(
            "ALTER TABLE `vms` ADD COLUMN `lifecycle_state` VARCHAR(16) "
            "NOT NULL DEFAULT 'active'"
        )

    # Index for the daily checker's WHERE expires_at <= now AND exempt = 0.
    idx = {
        row[0]
        for row in bind.exec_driver_sql(
            "SELECT index_name FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = 'vms'"
        )
    }
    if "ix_vms_expires_at" not in idx:
        op.execute("CREATE INDEX `ix_vms_expires_at` ON `vms` (`expires_at`)")

    # Backfill expiry for VMs that don't have one yet: created_at + 1 year
    # (fall back to now + 1 year if created_at is somehow NULL).
    op.execute(
        "UPDATE `vms` "
        "SET expires_at = DATE_ADD(COALESCE(created_at, UTC_TIMESTAMP()), INTERVAL 1 YEAR) "
        "WHERE expires_at IS NULL"
    )

    # Exempt discovered infrastructure VMs (no owner) so the TTL never
    # auto-deletes lab infra on first run. Admins opt them back in per-VM.
    op.execute("UPDATE `vms` SET exempt = 1 WHERE owner_user_id IS NULL")


def downgrade() -> None:
    bind = op.get_bind()
    idx = {
        row[0]
        for row in bind.exec_driver_sql(
            "SELECT index_name FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = 'vms'"
        )
    }
    if "ix_vms_expires_at" in idx:
        op.execute("DROP INDEX `ix_vms_expires_at` ON `vms`")
    for col in ("lifecycle_state", "exempt", "renewed_at", "expires_at"):
        op.execute(f"ALTER TABLE `vms` DROP COLUMN `{col}`")
