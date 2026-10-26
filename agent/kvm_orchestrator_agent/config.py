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

"""Agent configuration loader."""

import yaml


DEFAULT_CONFIG = {
    "controller_url": "https://kvm-orchestrator.example.com",
    "agent_key": "",
    "heartbeat_interval": 10,
    "verify_tls": True,
    "listen_port": 8444,
    "tls_cert": "/etc/kvm-orchestrator-agent/tls/agent.crt",
    "tls_key": "/etc/kvm-orchestrator-agent/tls/agent.key",
}


def load_config(path: str) -> dict:
    """Load configuration from YAML file, falling back to defaults."""
    config = DEFAULT_CONFIG.copy()
    try:
        with open(path) as f:
            file_config = yaml.safe_load(f) or {}
            config.update(file_config)
    except FileNotFoundError:
        pass
    return config
