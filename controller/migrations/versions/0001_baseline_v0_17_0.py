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

"""baseline v0.17.0

Revision ID: 0001
Revises:
Create Date: 2026-07-02

Baseline schema for kvm-orchestrator v0.17.0.

This revision snapshots the exact production Docker schema at v0.16.x so that
Alembic can carry forward from a known state. It creates the eight core tables:

    ssh_keys, users, tenants, networks, nodes, vms, user_tenants,
    user_ssh_pubkeys

For existing databases (Docker dev stack, or a K8s DB loaded from mysqldump),
this migration is applied via `alembic stamp 0001` — do NOT run `upgrade`, the
tables already exist. Only fresh databases run the CREATE TABLE statements.

Charset/collation is `utf8mb4 / utf8mb4_0900_ai_ci` throughout, InnoDB engine.
DDL is emitted verbatim as raw MySQL so future Alembic autogenerate diffs
compare against reality rather than the stale SQLAlchemy models.

HA schema additions (`ha_events` table, `vms.ha_restart`, `vms.ha_priority`)
land in revision 0002+ shipped with v0.18.0.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # ssh_keys — referenced by nodes.ssh_key_id
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE `ssh_keys` (
          `id` int NOT NULL AUTO_INCREMENT,
          `name` varchar(100) NOT NULL,
          `private_key` text NOT NULL,
          `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (`id`),
          UNIQUE KEY `name` (`name`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """
    )

    # ------------------------------------------------------------------
    # users — referenced by vms.owner_user_id, user_tenants.user_id,
    #         user_ssh_pubkeys.user_id
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE `users` (
          `id` int NOT NULL AUTO_INCREMENT,
          `username` varchar(100) NOT NULL,
          `email` varchar(255) DEFAULT NULL,
          `display_name` varchar(255) DEFAULT NULL,
          `password_hash` varchar(255) DEFAULT NULL,
          `auth_source` varchar(10) DEFAULT 'local',
          `ldap_dn` varchar(512) DEFAULT NULL,
          `is_active` tinyint(1) DEFAULT '1',
          `is_admin` tinyint(1) DEFAULT '0',
          `last_login_at` datetime DEFAULT NULL,
          `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (`id`),
          UNIQUE KEY `username` (`username`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """
    )

    # ------------------------------------------------------------------
    # tenants — referenced by vms.tenant_id, user_tenants.tenant_id
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE `tenants` (
          `id` int NOT NULL AUTO_INCREMENT,
          `name` varchar(100) NOT NULL,
          `ad_group_dn` varchar(512) DEFAULT NULL,
          `description` varchar(500) DEFAULT NULL,
          `is_builtin` tinyint(1) DEFAULT '0',
          `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (`id`),
          UNIQUE KEY `uq_tenant_name` (`name`),
          UNIQUE KEY `uq_tenant_ad_group` (`ad_group_dn`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """
    )

    # ------------------------------------------------------------------
    # networks — no foreign keys
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE `networks` (
          `id` int NOT NULL AUTO_INCREMENT,
          `name` varchar(100) NOT NULL,
          `bridge_name` varchar(64) NOT NULL,
          `vlan_id` int DEFAULT NULL,
          `description` text,
          `user_visible` tinyint(1) NOT NULL DEFAULT '0',
          `is_default` tinyint(1) NOT NULL DEFAULT '0',
          `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (`id`),
          UNIQUE KEY `uq_bridge_name` (`bridge_name`),
          KEY `idx_user_visible` (`user_visible`),
          KEY `idx_is_default` (`is_default`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """
    )

    # ------------------------------------------------------------------
    # nodes — FK: ssh_key_id → ssh_keys(id) ON DELETE SET NULL
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE `nodes` (
          `id` int NOT NULL AUTO_INCREMENT,
          `hostname` varchar(255) NOT NULL,
          `ip_address` varchar(45) NOT NULL,
          `ssh_host` varchar(255) DEFAULT NULL,
          `ssh_port` int DEFAULT '22',
          `ssh_user` varchar(100) DEFAULT NULL,
          `ssh_key_id` int DEFAULT NULL,
          `cpu_model` varchar(200) DEFAULT NULL,
          `port` int DEFAULT '8444',
          `status` varchar(20) DEFAULT 'offline',
          `total_vcpus` int DEFAULT '0',
          `total_ram_mb` int DEFAULT '0',
          `total_disk_gb` int DEFAULT '0',
          `available_vcpus` int DEFAULT '0',
          `available_ram_mb` int DEFAULT '0',
          `available_disk_gb` int DEFAULT '0',
          `agent_version` varchar(50) DEFAULT NULL,
          `last_heartbeat_at` datetime DEFAULT NULL,
          `ha_enabled` tinyint(1) DEFAULT '1',
          `ha_priority` int DEFAULT '100',
          `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
          `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (`id`),
          UNIQUE KEY `hostname` (`hostname`),
          KEY `ssh_key_id` (`ssh_key_id`),
          CONSTRAINT `nodes_ibfk_1` FOREIGN KEY (`ssh_key_id`)
              REFERENCES `ssh_keys` (`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """
    )

    # ------------------------------------------------------------------
    # vms — FKs: node_id → nodes, owner_user_id → users, tenant_id → tenants
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE `vms` (
          `id` int NOT NULL AUTO_INCREMENT,
          `node_id` int NOT NULL,
          `name` varchar(255) NOT NULL,
          `uuid` varchar(36) DEFAULT NULL,
          `state` varchar(20) DEFAULT 'unknown',
          `vcpus` int DEFAULT '0',
          `ram_mb` int DEFAULT '0',
          `vlan` varchar(100) DEFAULT NULL,
          `disk_gb` int DEFAULT '0',
          `disk_path` varchar(512) DEFAULT NULL,
          `datastore` varchar(255) DEFAULT NULL,
          `ip` varchar(45) DEFAULT NULL,
          `tenant_id` int DEFAULT NULL,
          `owner_user_id` int DEFAULT NULL,
          `last_seen_at` datetime DEFAULT NULL,
          `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
          `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (`id`),
          UNIQUE KEY `uq_node_name` (`node_id`,`name`),
          KEY `idx_node_id` (`node_id`),
          KEY `idx_vms_tenant` (`tenant_id`),
          KEY `idx_vms_owner` (`owner_user_id`),
          CONSTRAINT `vms_node_fk` FOREIGN KEY (`node_id`)
              REFERENCES `nodes` (`id`) ON DELETE CASCADE,
          CONSTRAINT `vms_owner_fk` FOREIGN KEY (`owner_user_id`)
              REFERENCES `users` (`id`) ON DELETE SET NULL,
          CONSTRAINT `vms_tenant_fk` FOREIGN KEY (`tenant_id`)
              REFERENCES `tenants` (`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """
    )

    # ------------------------------------------------------------------
    # user_tenants — FKs: user_id → users, tenant_id → tenants (both CASCADE)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE `user_tenants` (
          `user_id` int NOT NULL,
          `tenant_id` int NOT NULL,
          `synced_at` datetime DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (`user_id`,`tenant_id`),
          KEY `idx_user_tenants_tenant` (`tenant_id`),
          CONSTRAINT `user_tenants_tenant_fk` FOREIGN KEY (`tenant_id`)
              REFERENCES `tenants` (`id`) ON DELETE CASCADE,
          CONSTRAINT `user_tenants_user_fk` FOREIGN KEY (`user_id`)
              REFERENCES `users` (`id`) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """
    )

    # ------------------------------------------------------------------
    # user_ssh_pubkeys — FK: user_id → users ON DELETE CASCADE
    # Note: this table uses BIGINT PK (all others INT)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE `user_ssh_pubkeys` (
          `id` bigint NOT NULL AUTO_INCREMENT,
          `user_id` int NOT NULL,
          `fingerprint` varchar(64) NOT NULL,
          `pubkey` text NOT NULL,
          `comment` varchar(255) DEFAULT NULL,
          `created_at` datetime NOT NULL,
          `last_used_at` datetime NOT NULL,
          PRIMARY KEY (`id`),
          UNIQUE KEY `uq_user_fingerprint` (`user_id`,`fingerprint`),
          KEY `idx_user_last_used` (`user_id`,`last_used_at`),
          CONSTRAINT `user_ssh_pubkeys_user_fk` FOREIGN KEY (`user_id`)
              REFERENCES `users` (`id`) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """
    )


def downgrade() -> None:
    # Drop in reverse FK dependency order.
    op.execute("DROP TABLE IF EXISTS `user_ssh_pubkeys`")
    op.execute("DROP TABLE IF EXISTS `user_tenants`")
    op.execute("DROP TABLE IF EXISTS `vms`")
    op.execute("DROP TABLE IF EXISTS `nodes`")
    op.execute("DROP TABLE IF EXISTS `networks`")
    op.execute("DROP TABLE IF EXISTS `tenants`")
    op.execute("DROP TABLE IF EXISTS `users`")
    op.execute("DROP TABLE IF EXISTS `ssh_keys`")
