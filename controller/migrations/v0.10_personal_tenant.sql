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

-- v0.10 — Tenant visibility enforcement
-- Seeds the second built-in tenant "Personal VM" at id=2.
--
-- Visibility model after this migration:
--   tenant_id = NULL          → admin-only (untagged; only seeded by admins)
--   tenant_id = 1 (SHARED)    → visible to every authenticated user
--   tenant_id = 2 (PERSONAL)  → visible only to vms.owner_user_id
--   tenant_id = other (AD)    → visible to members of the bound AD group
--
-- All VMs that existed at v0.9 were already backfilled to SHARED. New VMs
-- created via the UI from v0.10 onwards default to SHARED with owner_user_id
-- = the creator. Sync-discovered VMs also default to SHARED on first INSERT.
-- Admins flip individual VMs to PERSONAL via Edit VM, optionally setting
-- owner_user_id to the user who should own them.

INSERT INTO tenants (id, name, ad_group_dn, description, is_builtin)
VALUES (2, 'Personal VM', NULL,
        'Personal VMs visible only to their owner', 1)
ON DUPLICATE KEY UPDATE name = name;
