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

"""
VM image catalog — golden RBD images used by Quick Create.

For v0.14.0 the catalog is hardcoded; the same five images that the
older self-service portal uses are listed verbatim. Each entry carries:

  id            stable, user-facing identifier (e.g. "ubuntu-2204")
  name          human-readable label for the UI dropdown
  os_family     "linux" | "windows" — drives cloud-init injection
  pool          Ceph RBD pool name on the node ("libvirt-pool")
  image         RBD image name to clone from
  default_user  cloud-init user created on Linux images
                (the API may override with the caller's CEC username)
  sizes         {"S": {vcpus, ram_mb, disk_gb}, "M": ..., "L": ...}

When growing past a handful of entries, replace this module with a small
DB table + an admin CRUD UI. The public API (`list_images`, `get_image`)
is shaped so the rest of the controller doesn't care about the backing
store.
"""

from typing import Any


# Ubuntu cloud images all create a default user named "ubuntu" via
# cloud-init. We expose this as the fallback default_user; the API layer
# typically overrides it with the caller's CEC username for friendlier
# `ssh youruser@vm` ergonomics.
_UBUNTU_SIZES: dict[str, dict[str, int]] = {
    "S": {"vcpus": 2, "ram_mb": 4096,  "disk_gb": 60},
    "M": {"vcpus": 4, "ram_mb": 8192,  "disk_gb": 80},
    "L": {"vcpus": 8, "ram_mb": 16384, "disk_gb": 120},
}

# Windows golden images are pre-sysprepped — cloud-init doesn't run, so
# default_user is unused. We keep the field present (empty) for catalog
# symmetry and so the frontend can render a single shape.
_WINDOWS_SIZES: dict[str, dict[str, int]] = {
    "S": {"vcpus": 4,  "ram_mb": 4096,  "disk_gb": 128},
    "M": {"vcpus": 8,  "ram_mb": 8192,  "disk_gb": 256},
    "L": {"vcpus": 16, "ram_mb": 16384, "disk_gb": 512},
}


IMAGES: list[dict[str, Any]] = [
    {
        "id":           "ubuntu-2004",
        "name":         "Ubuntu 20.04 LTS (Focal)",
        "os_family":    "linux",
        "pool":         "libvirt-pool",
        "image":        "focal-server-cloudimg-amd64",
        "default_user": "ubuntu",
        "sizes":        _UBUNTU_SIZES,
    },
    {
        "id":           "ubuntu-2204",
        "name":         "Ubuntu 22.04 LTS (Jammy)",
        "os_family":    "linux",
        "pool":         "libvirt-pool",
        "image":        "jammy-server-cloudimg-amd64",
        "default_user": "ubuntu",
        "sizes":        _UBUNTU_SIZES,
    },
    {
        "id":           "ubuntu-2404",
        "name":         "Ubuntu 24.04 LTS (Noble)",
        "os_family":    "linux",
        "pool":         "libvirt-pool",
        "image":        "noble-server-cloudimg-amd64",
        "default_user": "ubuntu",
        "sizes":        _UBUNTU_SIZES,
    },
    {
        "id":           "ubuntu-2604",
        "name":         "Ubuntu 26.04 LTS (Resolute)",
        "os_family":    "linux",
        "pool":         "libvirt-pool",
        "image":        "resolute-server-cloudimg-amd64",
        "default_user": "ubuntu",
        "sizes":        _UBUNTU_SIZES,
    },
    {
        "id":           "windows-10",
        "name":         "Windows 10 22H2 (sysprep, Sep 2023)",
        "os_family":    "windows",
        "pool":         "libvirt-pool",
        "image":        "windows-10-22h2_updated_sep_2023_v2",
        "default_user": "",
        "sizes":        _WINDOWS_SIZES,
    },
    {
        "id":           "windows-11",
        "name":         "Windows 11 22H2 (sysprep, Sep 2023)",
        "os_family":    "windows",
        "pool":         "libvirt-pool",
        "image":        "windows-11-22h2_updated_sep_2023_v2",
        "default_user": "",
        "sizes":        _WINDOWS_SIZES,
    },
]


def list_images() -> list[dict[str, Any]]:
    """Return a shallow copy of the catalog (callers may safely mutate)."""
    return [dict(img) for img in IMAGES]


def get_image(image_id: str) -> dict[str, Any] | None:
    """Lookup by stable id. Returns None on miss — caller decides how to fail."""
    for img in IMAGES:
        if img["id"] == image_id:
            return dict(img)
    return None


def get_size(image: dict[str, Any], size: str) -> dict[str, int] | None:
    """Return the {vcpus, ram_mb, disk_gb} dict for a T-shirt size on an
    image, or None if the size isn't defined for that image."""
    return image.get("sizes", {}).get(size)
