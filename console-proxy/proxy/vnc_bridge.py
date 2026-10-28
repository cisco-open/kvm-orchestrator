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

"""VNC bridge — WebSocket <-> VNC via SSH tunnel.

The VNC server on KVM hosts binds to 127.0.0.1, so we open a direct-tcpip
SSH channel to reach it rather than connecting to the node's public IP.
"""

import asyncio
import logging

import asyncssh

logger = logging.getLogger("console-proxy.vnc")


async def handle_vnc_session(websocket, token_data: dict) -> None:
    node_ip  = token_data["node_ip"]
    vnc_port = token_data["vnc_port"]
    ssh_user = token_data.get("ssh_user", "root")
    ssh_key  = token_data.get("ssh_key")

    if not ssh_key:
        logger.error("No SSH key in token for VNC session")
        await websocket.close(4010, "No SSH key for tunnel")
        return

    # Parse private key
    try:
        pkey = asyncssh.import_private_key(ssh_key)
    except Exception as exc:
        logger.error("Cannot import SSH private key: %s", exc)
        await websocket.close(4010, "Invalid SSH key")
        return

    # Open SSH connection to the node
    try:
        conn = await asyncssh.connect(
            node_ip,
            username=ssh_user,
            client_keys=[pkey],
            known_hosts=None,       # Internal lab nodes — no host verification
            connect_timeout=10,
        )
    except Exception as exc:
        logger.error("SSH connect to %s failed: %s", node_ip, exc)
        await websocket.close(4010, f"SSH connection failed: {exc}")
        return

    # Open a direct-tcpip channel to VNC on the remote localhost
    try:
        reader, writer = await conn.open_connection("127.0.0.1", vnc_port)
    except Exception as exc:
        logger.error("VNC tunnel to %s:127.0.0.1:%d failed: %s", node_ip, vnc_port, exc)
        conn.close()
        await websocket.close(4010, "Cannot reach VNC server")
        return

    logger.info("VNC tunnel established: %s -> 127.0.0.1:%d", node_ip, vnc_port)

    async def ws_to_vnc():
        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    writer.write(message)
                    await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()

    async def vnc_to_ws():
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                await websocket.send(data)
        except Exception:
            pass

    try:
        await asyncio.gather(ws_to_vnc(), vnc_to_ws())
    finally:
        conn.close()

    logger.info("VNC session ended: %s:%d", node_ip, vnc_port)
