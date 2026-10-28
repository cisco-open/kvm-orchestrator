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

"""default placement pool + two-pass placement semantics

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-05

Introduces the DEFAULT node placement pool (tenant id=4, is_builtin=1)
and separates it from ODaaSE.

Rationale
---------
Under 0002/v0.18-v0.19 the ODaaSE tenant carried two overlapping
concerns:

  * Node visibility  — every authenticated user can SEE ODaaSE-tagged
    nodes in the API/UI.
  * Placement fallback — any VM can be PLACED on an ODaaSE-tagged
    node, and the single-set eligible pool (`{ODaaSE} ∪ {caller}`)
    let ODaaSE nodes "steal" placements from tenant-dedicated nodes
    with less free RAM.

Going forward the two concerns are split:

  * **ODaaSE (id=3)** — a regular product tenant. Nodes tagged with
    ODaaSE serve ODaaSE-tenant VMs and are visible to ODaaSE members
    (and admins). No special placement or visibility semantics.
  * **DEFAULT (id=4, NEW)** — the shared / general placement pool.
    Nodes tagged with DEFAULT accept VMs from any tenant when the
    tenant has no dedicated node with capacity. Visible to all
    authenticated users. Replaces ODaaSE in the "visible to all
    nodes" auto-injection performed by
    `tenant_service.get_user_visibility()`.

Placement (v0.20+, in code) becomes a strict two-pass algorithm:

  1. Try nodes tagged with the caller's tenant. If any online node
     has enough vCPU/RAM/disk capacity, place there.
  2. Otherwise, try DEFAULT-tagged nodes. If any match, place there.
  3. Otherwise, HTTP 400.

Data step
---------
Every existing node is tagged with DEFAULT. This preserves the
current fleet-wide behaviour: any tenant without a dedicated node
still has fallback capacity. The pre-existing ODaaSE tags on those
nodes are left intact — harmless data that an admin can prune later
if they want ODaaSE to be truly product-specific on their fleet.

Idempotent seed: the DEFAULT INSERT uses ON DUPLICATE KEY UPDATE so
this migration is safe to re-apply.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DEFAULT_TENANT_ID = 4


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Seed the DEFAULT tenant.
    # ------------------------------------------------------------------
    # is_builtin=1 protects it from rename/delete via tenant_service.
    # ad_group_dn is NULL — not AD-backed. "Visible to all authenticated
    # users" and "placement fallback pool" are enforced in code, not by
    # AD group membership.
    op.execute(
        f"""
        INSERT INTO `tenants` (`id`, `name`, `ad_group_dn`, `description`, `is_builtin`)
        VALUES ({DEFAULT_TENANT_ID}, 'DEFAULT', NULL,
                'Default node placement pool — accepts VMs from any tenant '
                'when the tenant has no dedicated capacity. Visible to all '
                'authenticated users.', 1)
        ON DUPLICATE KEY UPDATE
          `name` = VALUES(`name`),
          `description` = VALUES(`description`),
          `is_builtin` = VALUES(`is_builtin`)
        """
    )

    # ------------------------------------------------------------------
    # Tag every existing node with DEFAULT — preserves the pre-migration
    # fleet-wide fallback semantic (any tenant can land on any node when
    # dedicated capacity is unavailable). Admins can un-tag specific
    # nodes from DEFAULT afterwards to make them tenant-dedicated only.
    #
    # Pre-existing ODaaSE tags on the same nodes are NOT removed — that
    # would be destructive. On a legacy fleet where 0002 auto-tagged
    # every node with ODaaSE, admins can now prune those tags manually
    # if they want ODaaSE to be truly product-specific.
    # ------------------------------------------------------------------
    op.execute(
        f"""
        INSERT IGNORE INTO `node_tenants` (`node_id`, `tenant_id`)
        SELECT `id`, {DEFAULT_TENANT_ID} FROM `nodes`
        """
    )


def downgrade() -> None:
    # Remove DEFAULT tags from all nodes, then the tenant row.
    # node_tenants FK is ON DELETE CASCADE so deleting the tenant row
    # alone would suffice, but the explicit DELETE keeps the downgrade
    # easy to reason about in migration logs.
    op.execute(
        f"DELETE FROM `node_tenants` WHERE `tenant_id` = {DEFAULT_TENANT_ID}"
    )
    op.execute(
        f"DELETE FROM `tenants` WHERE `id` = {DEFAULT_TENANT_ID}"
    )
