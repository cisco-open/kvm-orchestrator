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

"""Serial console bridge — WebSocket <-> Agent serial WebSocket."""

import asyncio
import logging

import websockets

logger = logging.getLogger("console-proxy.serial")


async def handle_serial_session(websocket, token_data: dict) -> None:
    """
    Bridge a browser WebSocket to the agent's serial console WebSocket.
    """
    node_ip = token_data["node_ip"]
    vm_id = token_data["vm_id"]
    agent_port = token_data.get("agent_port", 8444)

    agent_url = f"wss://{node_ip}:{agent_port}/vm/serial?vm_id={vm_id}"

    try:
        async with websockets.connect(agent_url, ssl=True) as agent_ws:
            logger.info("Serial bridge established: %s", agent_url)

            async def browser_to_agent():
                """Forward browser input to agent."""
                try:
                    async for message in websocket:
                        await agent_ws.send(message)
                except Exception:
                    pass

            async def agent_to_browser():
                """Forward agent output to browser."""
                try:
                    async for message in agent_ws:
                        await websocket.send(message)
                except Exception:
                    pass

            await asyncio.gather(browser_to_agent(), agent_to_browser())

    except (ConnectionRefusedError, OSError) as e:
        logger.error("Cannot connect to agent serial: %s", e)
        await websocket.close(4010, "Cannot reach agent")
        return

    logger.info("Serial session ended: vm=%s", vm_id)
