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
