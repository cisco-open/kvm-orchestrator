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

"""Heartbeat sender — periodically reports metrics to the controller."""

import logging
import time

import requests

from .metrics import collect_metrics

logger = logging.getLogger("kvm-orchestrator-agent.heartbeat")


def start_heartbeat(config: dict) -> None:
    """Run heartbeat loop (called in a daemon thread)."""
    interval = config["heartbeat_interval"]
    url = f"{config['controller_url']}/api/agent/heartbeat"
    headers = {"X-Agent-Key": config["agent_key"]}
    # TLS verification. Defaults to True; set verify_tls: false in the config
    # for lab controllers presenting a cert without a trusted CA chain.
    verify = config.get("verify_tls", True)
    if verify is False:
        # Silence the noisy per-request InsecureRequestWarning.
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass
        logger.warning("TLS verification DISABLED (verify_tls: false)")

    while True:
        try:
            metrics = collect_metrics()
            response = requests.post(
                url, json=metrics, headers=headers, timeout=10, verify=verify
            )
            if response.status_code != 200:
                logger.warning(
                    "Heartbeat rejected: %s %s",
                    response.status_code, response.text[:200],
                )
        except requests.RequestException as e:
            logger.error("Heartbeat failed: %s", e)

        time.sleep(interval)
