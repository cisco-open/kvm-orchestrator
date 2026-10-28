-- SPDX-License-Identifier: Apache-2.0
--
-- Copyright 2026 Cisco Systems, Inc. and its affiliates
--
-- Licensed under the Apache License, Version 2.0 (the "License");
-- you may not use this file except in compliance with the License.
-- You may obtain a copy of the License at
--
-- http://www.apache.org/licenses/LICENSE-2.0
--
-- Unless required by applicable law or agreed to in writing, software
-- distributed under the License is distributed on an "AS IS" BASIS,
-- WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
-- See the License for the specific language governing permissions and
-- limitations under the License.

-- v0.9 — Tenancy foundation
-- Adds tenants table, user_tenants membership cache, and tenant_id/owner_user_id on vms.
-- Seeds the built-in SHARED tenant and backfills all existing VMs to it.

-- 1. tenants table
CREATE TABLE IF NOT EXISTS tenants (
  id           INT          NOT NULL AUTO_INCREMENT,
  name         VARCHAR(100) NOT NULL,
  ad_group_dn  VARCHAR(512) NULL,
  description  VARCHAR(500) NULL,
  is_builtin   TINYINT(1)   DEFAULT 0,
  created_at   DATETIME     DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_tenant_name (name),
  UNIQUE KEY uq_tenant_ad_group (ad_group_dn)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 2. Seed the built-in SHARED tenant at id=1
INSERT INTO tenants (id, name, ad_group_dn, description, is_builtin)
VALUES (1, 'SHARED', NULL,
        'Shared lab VMs visible to all authenticated users', 1)
ON DUPLICATE KEY UPDATE name = name;

-- 3. user_tenants membership cache (refreshed from AD on login)
CREATE TABLE IF NOT EXISTS user_tenants (
  user_id   INT      NOT NULL,
  tenant_id INT      NOT NULL,
  synced_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id, tenant_id),
  KEY idx_user_tenants_tenant (tenant_id),
  CONSTRAINT user_tenants_user_fk
    FOREIGN KEY (user_id)   REFERENCES users(id)   ON DELETE CASCADE,
  CONSTRAINT user_tenants_tenant_fk
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 4. vms: add tenant_id (nullable = personal) + owner_user_id
ALTER TABLE vms
  ADD COLUMN tenant_id     INT NULL AFTER ip,
  ADD COLUMN owner_user_id INT NULL AFTER tenant_id,
  ADD KEY idx_vms_tenant (tenant_id),
  ADD KEY idx_vms_owner  (owner_user_id),
  ADD CONSTRAINT vms_tenant_fk
    FOREIGN KEY (tenant_id)     REFERENCES tenants(id) ON DELETE SET NULL,
  ADD CONSTRAINT vms_owner_fk
    FOREIGN KEY (owner_user_id) REFERENCES users(id)   ON DELETE SET NULL;

-- 5. Backfill: every existing VM goes to SHARED (id=1).
-- They were all discovered by vm_sync (no known creator), so owner_user_id stays NULL.
UPDATE vms SET tenant_id = 1 WHERE tenant_id IS NULL;
