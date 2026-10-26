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

"""Console proxy entry point — asyncio WebSocket server."""

import asyncio
import logging
import os
import signal

import websockets

from .token_auth import validate_token
from .vnc_bridge import handle_vnc_session
from .serial_bridge import handle_serial_session
from .version import VERSION, RELEASE_NOTE

logger = logging.getLogger("console-proxy")

LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "8765"))
MAX_SESSION_HOURS = int(os.environ.get("MAX_SESSION_HOURS", "8"))


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

    logger.info(
        "Console session: vm=%s type=%s user=%s",
        token_data["vm_id"],
        token_data["type"],
        token_data["user_id"],
    )

    # Route to appropriate bridge
    if token_data["type"] == "vnc":
        await handle_vnc_session(websocket, token_data)
    elif token_data["type"] == "serial":
        await handle_serial_session(websocket, token_data)
    else:
        await websocket.close(4003, "Unknown console type")


async def main():
    """Start the WebSocket server."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

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
