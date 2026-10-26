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
