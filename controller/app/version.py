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

VERSION = "v0.35.7"
RELEASE_NOTE = "vm_sync single-flight per node (Redis lock) + touch-only unchanged VMs (no lock waits / binlog flood); readable event logging with user, real client IP (TRUSTED_PROXIES) and request ID, nginx real_ip + X-Request-ID + clean access log, console open/close lines; MySQL binlog off by default (helm mysql.disableBinlog); node detail page uses the shared VM table."
