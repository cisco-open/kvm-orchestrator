"""Agent entry point."""

import argparse
import logging
import threading

from .config import load_config
from .heartbeat import start_heartbeat

logger = logging.getLogger("kvm-orchestrator-agent")


def main():
    """Main entry point for kvm-orchestrator-agent."""
    parser = argparse.ArgumentParser(description="KVM Orchestrator Node Agent")
    parser.add_argument(
        "-c",
        "--config",
        default="/etc/kvm-orchestrator-agent/config.yaml",
        help="Path to config file",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging"
    )
    parser.add_argument(
        "--no-api",
        action="store_true",
        help="Heartbeat only — do not start the command API server "
        "(skips the flask/libvirt dependency).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    config = load_config(args.config)
    logger.info("kvm-orchestrator-agent starting (controller: %s)", config["controller_url"])

    # Heartbeat is the core HA-critical function and has minimal deps
    # (requests + psutil). It runs in the main thread so the process lives
    # and fails loudly if it dies.
    if args.no_api:
        logger.info("Running in heartbeat-only mode (--no-api)")
        start_heartbeat(config)
        return

    # Optional command API server (flask + libvirt). If those deps are not
    # installed we degrade to heartbeat-only rather than crash — heartbeat
    # is what HA failure-detection depends on.
    try:
        from .api_server import start_api_server
    except Exception as e:  # noqa: BLE001 - missing flask/libvirt etc.
        logger.warning(
            "Command API unavailable (%s) — running heartbeat-only", e
        )
        start_heartbeat(config)
        return

    heartbeat_thread = threading.Thread(
        target=start_heartbeat, args=(config,), daemon=True
    )
    heartbeat_thread.start()
    start_api_server(config)


if __name__ == "__main__":
    main()
