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

VERSION = "v0.35.4"
RELEASE_NOTE = "(1) vm_sync no longer wipes admin-set VM metadata on a reap+reinsert — _do_upsert snapshots tenant_id/owner_user_id/priority/exempt/expires_at/renewed_at/lifecycle_state by the stable libvirt uuid before the upsert+reap and re-applies them after, so admin tenancy/owner/priority/exempt/TTL survive node outages and reaps. (2) VMs page: every column is now sortable (name/state/host/ip/vcpus/ram/network/disk/datastore/priority/tenant/expires), type-aware (numeric for vcpus/ram/disk/ip/expires; priority by severity). (3) Removed the duplicate lifecycle badge next to State; lifecycle is shown only in the single 'Expires' column."
