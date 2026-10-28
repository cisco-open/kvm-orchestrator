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

"""node tenants + ODaaSE

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-03

Adds per-tenant node restriction.

Nodes gain a many-to-many relationship to tenants via a new `node_tenants`
join table (mirror of `user_tenants`). Non-admin users only see nodes tagged
with at least one of their tenants, or with the special ODaaSE tenant.

Introduces ODaaSE (id=3, is_builtin=1) — a shared host pool tenant with
"visible to all authenticated users" semantics for NODES. Analogous to
SHARED (id=1) which carries that semantic for VMs. Both are decoupled on
purpose: a node can be public without changing VM visibility rules.

Data step: every existing node is tagged with ODaaSE, preserving the
pre-migration visibility rule ("everyone sees every node") for existing
fleets. Admins can then restrict individual nodes by unticking ODaaSE and
picking specific tenants.

Idempotent seed: the ODaaSE INSERT uses ON DUPLICATE KEY UPDATE so this
migration is safe to run against DBs that already have an ODaaSE row from
a previous manual seed.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ODAASE_TENANT_ID = 3


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Seed the ODaaSE tenant.
    # ------------------------------------------------------------------
    # is_builtin=1 protects it from rename/delete via tenant_service.
    # ad_group_dn is NULL because the tenant is not AD-backed — its
    # "visible to all authenticated users" semantic is enforced in code
    # (tenant_service.get_user_visibility injects the ID into every
    # user's node_tenant_ids).
    op.execute(
        f"""
        INSERT INTO `tenants` (`id`, `name`, `ad_group_dn`, `description`, `is_builtin`)
        VALUES ({ODAASE_TENANT_ID}, 'ODaaSE', NULL,
                'Shared host pool visible to all authenticated users', 1)
        ON DUPLICATE KEY UPDATE
          `name` = VALUES(`name`),
          `description` = VALUES(`description`),
          `is_builtin` = VALUES(`is_builtin`)
        """
    )

    # ------------------------------------------------------------------
    # node_tenants — FKs: node_id → nodes, tenant_id → tenants (both CASCADE)
    # Mirrors user_tenants exactly. Composite PK, no synthetic id.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE `node_tenants` (
          `node_id` int NOT NULL,
          `tenant_id` int NOT NULL,
          `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (`node_id`,`tenant_id`),
          KEY `idx_node_tenants_tenant` (`tenant_id`),
          CONSTRAINT `node_tenants_node_fk` FOREIGN KEY (`node_id`)
              REFERENCES `nodes` (`id`) ON DELETE CASCADE,
          CONSTRAINT `node_tenants_tenant_fk` FOREIGN KEY (`tenant_id`)
              REFERENCES `tenants` (`id`) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """
    )

    # ------------------------------------------------------------------
    # Tag every existing node with ODaaSE — preserves pre-migration
    # visibility (all nodes visible to all users) as a safe default.
    # Admins can then restrict.
    # ------------------------------------------------------------------
    op.execute(
        f"""
        INSERT IGNORE INTO `node_tenants` (`node_id`, `tenant_id`)
        SELECT `id`, {ODAASE_TENANT_ID} FROM `nodes`
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS `node_tenants`")
    # Leave the ODaaSE tenant row in place on downgrade — removing it
    # could orphan node_tenants rows that a future re-upgrade would want
    # to preserve. Belt-and-braces: if the row genuinely needs to go,
    # do it manually.
