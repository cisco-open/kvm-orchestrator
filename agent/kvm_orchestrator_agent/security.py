"""Security helpers — agent key and TLS cert handling."""

import os


def validate_agent_key(provided_key: str, config: dict) -> bool:
    """Validate the provided agent key against the configured key."""
    expected = config.get("agent_key", "")
    if not expected:
        return False
    return provided_key == expected
