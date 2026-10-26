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

"""VM lifecycle expiry task (Celery Beat).

Runs the 1-year TTL grace machine once a day: warn owners approaching expiry,
auto-stop at expiry, auto-delete after the final grace. Gated by
LIFECYCLE_ENABLED (off by default) so it is a safe no-op until armed.
"""

from ..extensions import celery


@celery.task(name="app.tasks.vm_expiry.check_vm_expiry")
def check_vm_expiry():
    from ..services.lifecycle_engine import run_expiry_check

    return run_expiry_check()
