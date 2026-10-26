# SPDX-License-Identifier: Apache-2.0
#
# Copyright [2026] [Cisco Systems, Inc. and its affiliates]
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

"""HA VM relocation tasks."""

from ..extensions import celery


@celery.task(name="app.tasks.ha_relocate.relocate_node_vms")
def relocate_node_vms(failed_node_id: int):
    """Relocate all HA-enabled VMs from a failed node."""
    from ..services.ha_engine import relocate_vms

    relocate_vms(failed_node_id)
