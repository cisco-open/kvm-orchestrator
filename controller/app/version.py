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

VERSION = "v0.35.2"
RELEASE_NOTE = "Cloud-init seed on SHARED Ceph RBD. Quick Create used to write each Linux VM's cloud-init seed ISO to the node's LOCAL filesystem (/var/lib/libvirt/images/wvc-seed/<name>.iso) and attach it as a file-backed cdrom. That stranded data on one host and broke live migration ('Cannot access storage file .../wvc-seed/<name>.iso: No such file or directory') because the target node has no such file/dir. Now the seed ISO is built to a temp file, imported into the shared Ceph pool as '<name>-seed', and attached as a NETWORK (rbd) read-only cdrom — so ALL VM data lives on shared storage and the VM is always live-migratable (_is_shared_disk treats type=network as shared). Delete now also rbd-rm's the '<name>-seed' image. For EXISTING VMs that still carry a local seed, _live_migrate now detaches the spent seed cdrom (a one-time first-boot artifact) before migrating, so they migrate cleanly too. No schema or API-contract change."
