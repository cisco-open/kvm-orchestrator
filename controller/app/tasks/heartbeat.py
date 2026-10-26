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

"""Heartbeat monitoring task (Celery Beat)."""

from ..extensions import celery


@celery.task(name="app.tasks.heartbeat.check_node_heartbeats")
def check_node_heartbeats():
    """
    Periodic task: check all HA-enabled nodes for stale heartbeats.
    Runs every 15 seconds via Celery Beat.
    """
    from ..services.ha_engine import check_node_heartbeats as _check

    _check()
