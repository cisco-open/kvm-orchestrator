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

"""Async VM operation tasks."""

from ..extensions import celery


@celery.task(name="app.tasks.vm_ops.vm_action")
def vm_action(vm_id: int, action: str):
    """Execute a VM action asynchronously (start/stop/reboot/destroy)."""
    from ..services.vm_service import send_vm_action

    return send_vm_action(vm_id, action)


@celery.task(name="app.tasks.vm_ops.vm_migrate")
def vm_migrate(vm_id: int, target_node_id: int):
    """Execute a live migration."""
    # TODO: Implement migration logic
    pass
