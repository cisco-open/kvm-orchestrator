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

"""kvm-orchestrator-agent package setup."""

from setuptools import setup, find_packages

setup(
    name="kvm-orchestrator-agent",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "flask>=3.1",
        "requests>=2.32",
        "psutil>=6.1",
        "pyyaml>=6.0",
        "libvirt-python>=10.9",
        "cryptography>=44",
    ],
    entry_points={
        "console_scripts": [
            "kvm-orchestrator-agent=kvm_orchestrator_agent.main:main",
        ],
    },
    python_requires=">=3.11",
)
