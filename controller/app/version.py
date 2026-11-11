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

VERSION = "v0.35.5"
RELEASE_NOTE = "Lifecycle: continuous enrollment backfill in the daily checker — every non-exempt VM lacking a term gets expires_at = created_at + TTL on each run (migration 0014 only did this once at install; vm_sync inserts newly-discovered VMs with expires_at=NULL, which would otherwise escape lifecycle forever = 'limbo'). Runs regardless of the enforcement switch; exempt VMs left NULL. Pairs with the uuid-keyed metadata preservation in vm_sync."
