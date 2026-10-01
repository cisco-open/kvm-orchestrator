"""add ha_settings runtime kill-switch table

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-04

Creates a small key/value settings table that holds the AUTHORITATIVE runtime
HA on/off switch, checked at the top of every check_node_heartbeats cycle so
ops can flip HA from the admin UI without a redeploy.

  ha_settings(
    name   VARCHAR(64) PRIMARY KEY,
    value  VARCHAR(255) NOT NULL,
    updated_at DATETIME,
    updated_by VARCHAR(255) NULL
  )

Seeded with a single row:
  name='ha_enabled', value=<env HA_ENABLED at first migrate, default 'false'>

Rationale: the config env var HA_ENABLED is only a STARTUP default; this row is
the live switch. Kept generic (name/value) so future runtime flags (e.g.
ha_allow_execute) can reuse the table.

Idempotent: guards CREATE TABLE + the seed INSERT so it can be re-run.
"""
import os
from typing import Sequence, Union

from alembic import op


revision: str = "0009"
down_revision: Union[str, None] = "0008"
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
    if "ha_settings" not in tables:
        op.execute(
            "CREATE TABLE `ha_settings` ("
            "  `name` VARCHAR(64) NOT NULL PRIMARY KEY,"
            "  `value` VARCHAR(255) NOT NULL,"
            "  `updated_at` DATETIME NULL,"
            "  `updated_by` VARCHAR(255) NULL"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        )

    # Seed the ha_enabled row from the env default the FIRST time only.
    seed = "true" if os.environ.get("HA_ENABLED", "false").lower() == "true" else "false"
    op.execute(
        "INSERT INTO `ha_settings` (`name`, `value`, `updated_at`, `updated_by`) "
        f"SELECT 'ha_enabled', '{seed}', NOW(), 'migration-0008' "
        "FROM DUAL WHERE NOT EXISTS "
        "(SELECT 1 FROM `ha_settings` WHERE `name` = 'ha_enabled')"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS `ha_settings`")
