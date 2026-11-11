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

VERSION = "v0.35.3"
RELEASE_NOTE = "VMs page: new toggleable 'Expires' column showing each VM's remaining 1-year-TTL days ('Nd left', amber within 30d, 'Exempt'/'Expired'/'—' as applicable). Previously lifecycle only surfaced as a small badge next to State that showed a day-count only within the final 30 days, so TTL status was easy to miss. Builds on v0.35.2 (cloud-init seed on shared Ceph RBD + live-migrate detaches spent local seeds)."
