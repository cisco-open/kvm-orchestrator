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

"""Console proxy entry point — asyncio WebSocket server."""

import asyncio
import logging
import os
import ipaddress
import signal
import time

import websockets

from .token_auth import validate_token
from .vnc_bridge import handle_vnc_session
from .serial_bridge import handle_serial_session
from .version import VERSION, RELEASE_NOTE

logger = logging.getLogger("console-proxy")

LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "8765"))
MAX_SESSION_HOURS = int(os.environ.get("MAX_SESSION_HOURS", "8"))


def _parse_trusted(value: str) -> tuple:
    nets = []
    for part in value.split(","):
        part = part.strip()
        if part:
            try:
                nets.append(ipaddress.ip_network(part, strict=False))
            except ValueError:
                logger.warning("TRUSTED_PROXIES: ignoring invalid entry %r", part)
    return tuple(nets)


# Proxies whose X-Forwarded-For entries are trusted (comma-separated CIDRs).
# Default = k3s/RKE2 default pod CIDR + loopback; set to your cluster's pod CIDR.
_TRUSTED = _parse_trusted(os.environ.get("TRUSTED_PROXIES", "10.42.0.0/16,127.0.0.1"))


def _client_ip(websocket) -> str:
    """First untrusted hop in X-Forwarded-For (right to left after the peer)."""
    peer = websocket.remote_address[0] if websocket.remote_address else "unknown"
    try:
        xff = websocket.request.headers.get("X-Forwarded-For", "")
    except Exception:
        xff = ""
    hops = [h.strip() for h in xff.split(",") if h.strip()] + [peer]
    for hop in reversed(hops):
        try:
            addr = ipaddress.ip_address(hop)
        except ValueError:
            return hop
        if not any(addr in n for n in _TRUSTED):
            return hop
    return hops[0]


def _request_id(websocket) -> str:
    try:
        return websocket.request.headers.get("X-Request-ID", "-") or "-"
    except Exception:
        return "-"


async def handler(websocket):
    """Handle incoming WebSocket connection."""
    # Extract token from query params
    path = websocket.request.path if hasattr(websocket, "request") else ""
    params = dict(
        pair.split("=", 1)
        for pair in (websocket.request.path.split("?", 1)[1] if "?" in websocket.request.path else "").split("&")
        if "=" in pair
    )
    token = params.get("token")

    if not token:
        await websocket.close(4001, "Missing token")
        return

    # Validate token (single-use, from Redis)
    token_data = await validate_token(token)
    if token_data is None:
        await websocket.close(4002, "Invalid or expired token")
        return

    ip = _client_ip(websocket)
    rid = _request_id(websocket)
    user = token_data.get("username") or f"user#{token_data['user_id']}"
    vm = token_data.get("vm_name") or f"#{token_data['vm_id']}"
    kind = str(token_data["type"]).upper()
    logger.info("%s connected to the %s console of VM %s from %s [%s]", user, kind, vm, ip, rid)
    started = time.monotonic()

    # Route to appropriate bridge
    try:
        if token_data["type"] == "vnc":
            await handle_vnc_session(websocket, token_data)
        elif token_data["type"] == "serial":
            await handle_serial_session(websocket, token_data)
        else:
            await websocket.close(4003, "Unknown console type")
    finally:
        logger.info("%s closed the %s console of VM %s after %ds from %s [%s]",
                    user, kind, vm, int(time.monotonic() - started), ip, rid)


async def main():
    """Start the WebSocket server."""
    level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="[%(asctime)s] %(levelname)-8s kvm-orchestrator :: %(message)s",
    )
    # Per-connection handshake chatter (and probes) only at DEBUG.
    logging.getLogger("websockets").setLevel(max(level, logging.WARNING))

    stop = asyncio.get_event_loop().create_future()

    def shutdown():
        stop.set_result(None)

    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGTERM, shutdown)
    loop.add_signal_handler(signal.SIGINT, shutdown)

    async with websockets.serve(handler, LISTEN_HOST, LISTEN_PORT):
        logger.info("kvm-orchestrator-console-proxy %s — %s", VERSION, RELEASE_NOTE)
        logger.info("Console proxy listening on %s:%d", LISTEN_HOST, LISTEN_PORT)
        await stop


if __name__ == "__main__":
    asyncio.run(main())
