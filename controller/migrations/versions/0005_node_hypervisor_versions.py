"""add hypervisor version columns to nodes (kvm, qemu, libvirt, kernel)

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-14

Adds four nullable metadata columns to `nodes`, populated on every
successful node poll by node_monitor:

  * kvm_version       — output of `modinfo kvm` -F version (e.g. "kvm")
                        or empty if the kvm module isn't loaded. In
                        practice this stays fixed to the kernel's
                        in-tree kvm.ko so we store the kernel release
                        instead when the module reports "N/A" or blank.
  * qemu_version      — `qemu-system-x86_64 --version | head -1 | awk`
                        e.g. "9.2.0" or "8.2.2"
  * libvirt_version   — `virsh --version` (short form, e.g. "10.0.0")
  * kernel_version    — `uname -r` (e.g. "6.8.0-40-generic") — cheap
                        adjunct, useful when triaging kvm-module bugs

All columns are metadata only (nullable, default NULL). No FK, no index —
displayed on the NodeDetail page as a passive "Hypervisor" info block.

Idempotent: uses INFORMATION_SCHEMA to guard the ADD COLUMN so
`alembic upgrade 0005` can be re-run.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_COLS = [
    ("kvm_version",     "VARCHAR(50) NULL"),
    ("qemu_version",    "VARCHAR(50) NULL"),
    ("libvirt_version", "VARCHAR(50) NULL"),
    ("kernel_version",  "VARCHAR(100) NULL"),
]


def upgrade() -> None:
    bind = op.get_bind()
    existing = {
        row[0]
        for row in bind.exec_driver_sql(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'nodes'"
        )
    }
    for col, spec in _NEW_COLS:
        if col not in existing:
            op.execute(f"ALTER TABLE `nodes` ADD COLUMN `{col}` {spec}")


def downgrade() -> None:
    for col, _ in reversed(_NEW_COLS):
        op.execute(f"ALTER TABLE `nodes` DROP COLUMN `{col}`")
