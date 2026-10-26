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

"""Network auto-discovery task (Celery Beat).

Periodically scans online nodes for OVS VLAN bridges and adds any missing
ones to the networks catalog (hidden, pending admin review). ADD-only — it
never modifies or deletes existing catalog rows.
"""

from ..extensions import celery


@celery.task(name="app.tasks.network_discovery.discover_networks")
def discover_networks():
    """Periodic task: import newly-provisioned VLAN bridges into the catalog."""
    from ..services.network_service import discover_networks as _discover

    return _discover()
