"""merge Personal VM (id=2) into ODaaSE (id=3)

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-07

Consolidates the two "user's-own-VM" tenants into one. Product decision:
"Personal VM" and "ODaaSE" are the same thing — a user's private
developer VM in the ODaaSE product line. Going forward there is one
tenant, id=3, named "ODaaSE", with the owner-only visibility rule that
`Personal VM` (id=2) used to carry.

Semantics change
----------------
Pre-0004 (v0.20.x):
  * id=2 "Personal VM"    — visible to `owner_user_id` + admins only.
  * id=3 "ODaaSE"         — regular product tenant; visible to members
                            of its AD group (none — `ad_group_dn` is
                            NULL, so effectively admin-only today).

Post-0004 (v0.21.0):
  * id=3 "ODaaSE"         — visible to `owner_user_id` + admins only
                            (adopts what was Personal VM's rule).
  * id=2                  — deleted from the `tenants` catalog.

Enforced in code by flipping `PERSONAL_TENANT_ID` from 2 to 3 in
`controller/app/services/tenant_service.py`; the `_can_see_vm` +
`list_vms` predicates re-target automatically via the constant.

Data steps
----------
1. Move the 126 personal VMs (`vms.tenant_id = 2`) onto id=3. Owner
   already set on all of them (v0.11.3 invariant + the 2026-07-07
   reconcile run stamped the 123 remaining SHARED-orphans with owners).

2. Drop the 17 stale `node_tenants` rows tagging nodes as ODaaSE. Those
   were seeded by migration 0002 to make ODaaSE nodes "visible to all
   users"; that role moved to DEFAULT (id=4) in 0003. Every one of the
   17 nodes is ALSO tagged DEFAULT, so removing the ODaaSE tag is a
   zero-visibility-change cleanup. Once id=3 becomes an owner-only
   tenant, `node_tenants` rows referencing it are semantically wrong
   (owner-only tenants never gate node visibility).

3. Delete the `tenants` row for id=2. The `vms.tenant_id` FK is
   `ON DELETE SET NULL`, and step 1 already emptied the column, so no
   cascade happens.

Idempotency
-----------
All three DELETEs / UPDATEs are re-runnable no-ops after the first
successful apply. Safe against a re-run of `alembic upgrade 0004`.

Downgrade
---------
Full re-hydrate. Re-inserts the Personal VM (id=2) tenant row (identical
to the shape the 0001 baseline seeds), re-tags every existing node with
ODaaSE (id=3), and moves any VMs still on id=3 that lack an owner...
actually no — we cannot know which of the id=3 VMs used to be id=2 and
which were legitimate ODaaSE product-tenant VMs. Downgrade therefore
restores the tenant catalog + node tags, but leaves the VMs on id=3.
Callers wanting the exact pre-0004 VM assignment must snapshot before
running upgrade().
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PERSONAL_OLD_ID = 2   # "Personal VM" — deleted in this migration
ODAASE_ID = 3         # new owner-only tenant (formerly product tenant)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Move all personal VMs to the ODaaSE tenant.
    # ------------------------------------------------------------------
    # Idempotent: after the first run, zero rows have tenant_id=2, so
    # the UPDATE is a no-op.
    op.execute(
        f"""
        UPDATE `vms`
           SET `tenant_id` = {ODAASE_ID}
         WHERE `tenant_id` = {PERSONAL_OLD_ID}
        """
    )

    # ------------------------------------------------------------------
    # 2. Drop stale ODaaSE node tags.
    # ------------------------------------------------------------------
    # Every node that was tagged ODaaSE (id=3) by migration 0002 is also
    # tagged DEFAULT (id=4) by 0003 — verified against the prod DB on
    # 2026-07-07 (17/17 nodes). Removing the ODaaSE tag is a
    # zero-visibility-change cleanup. Once id=3 becomes owner-only, its
    # node_tenants rows carry no meaning and would clutter the Nodes
    # page's tenant-chip strip.
    op.execute(
        f"""
        DELETE FROM `node_tenants`
         WHERE `tenant_id` = {ODAASE_ID}
        """
    )

    # ------------------------------------------------------------------
    # 3. Delete the Personal VM tenant row.
    # ------------------------------------------------------------------
    # vms.tenant_id FK is ON DELETE SET NULL — step 1 already emptied
    # the column, so no cascade happens. user_tenants.tenant_id and
    # node_tenants.tenant_id FKs are ON DELETE CASCADE, but both tables
    # have zero rows for id=2 (Personal is never AD-mapped and is a
    # VM-scoped concept, not a node one).
    op.execute(
        f"""
        DELETE FROM `tenants`
         WHERE `id` = {PERSONAL_OLD_ID}
        """
    )


def downgrade() -> None:
    # Re-insert the Personal VM (id=2) tenant row, mirroring the shape
    # the 0001 baseline seeded. Ordering matters only in that we insert
    # the tenant row before re-tagging nodes (FK reference).
    op.execute(
        f"""
        INSERT INTO `tenants` (`id`, `name`, `ad_group_dn`, `description`, `is_builtin`)
        VALUES ({PERSONAL_OLD_ID}, 'Personal VM', NULL,
                'Owner-only VM tenant. Visible to vms.owner_user_id + admins.', 1)
        ON DUPLICATE KEY UPDATE
          `name` = VALUES(`name`),
          `description` = VALUES(`description`),
          `is_builtin` = VALUES(`is_builtin`)
        """
    )

    # Re-tag every existing node with ODaaSE (id=3), matching 0002's
    # data step. INSERT IGNORE handles the case where the tag survived.
    op.execute(
        f"""
        INSERT IGNORE INTO `node_tenants` (`node_id`, `tenant_id`)
        SELECT `id`, {ODAASE_ID} FROM `nodes`
        """
    )

    # Note: we deliberately do NOT move VMs back from id=3 to id=2.
    # We cannot tell which id=3 rows were formerly id=2 and which were
    # legitimate ODaaSE product-tenant VMs. Callers that need the exact
    # pre-upgrade VM assignment must restore from the nightly mysqldump
    # backup at backup.example.com:/data/backup/
    # kvm-orchestrator/.
