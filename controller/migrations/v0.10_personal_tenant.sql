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
