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

"""Celery worker entry point."""

from app import create_app
from app.extensions import celery

# Import tasks so they get registered
import app.tasks.heartbeat  # noqa: F401
import app.tasks.ha_relocate  # noqa: F401
import app.tasks.group_sync  # noqa: F401
import app.tasks.vm_ops  # noqa: F401
import app.tasks.vm_expiry  # noqa: F401
import app.tasks.network_discovery  # noqa: F401

flask_app = create_app()
flask_app.app_context().push()
