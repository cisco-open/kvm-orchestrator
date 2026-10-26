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

"""Minimal API server for receiving commands from the controller."""

import logging

from flask import Flask, jsonify, request

logger = logging.getLogger("kvm-orchestrator-agent.api")

app = Flask(__name__)


def _verify_agent_key():
    """Verify controller's agent key."""
    # TODO: Check X-Agent-Key header
    return True


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok"}), 200


@app.route("/vm/define", methods=["POST"])
def vm_define():
    """Define (create) a VM from XML."""
    data = request.get_json()
    from .libvirt_ops import define_vm

    result = define_vm(data.get("xml"), data.get("vm_id"))
    return jsonify(result), 200


@app.route("/vm/start", methods=["POST"])
def vm_start():
    """Start a VM."""
    data = request.get_json()
    from .libvirt_ops import start_vm

    result = start_vm(data.get("name"))
    return jsonify(result), 200


@app.route("/vm/stop", methods=["POST"])
def vm_stop():
    """Gracefully stop a VM."""
    data = request.get_json()
    from .libvirt_ops import stop_vm

    result = stop_vm(data.get("name"))
    return jsonify(result), 200


@app.route("/vm/destroy", methods=["POST"])
def vm_destroy():
    """Force-stop a VM."""
    data = request.get_json()
    from .libvirt_ops import destroy_vm

    result = destroy_vm(data.get("name"))
    return jsonify(result), 200


@app.route("/vm/reboot", methods=["POST"])
def vm_reboot():
    """Reboot a VM."""
    data = request.get_json()
    from .libvirt_ops import reboot_vm

    result = reboot_vm(data.get("name"))
    return jsonify(result), 200


@app.route("/vm/list", methods=["GET"])
def vm_list():
    """List managed VMs on this node."""
    from .libvirt_ops import list_managed_vms

    vms = list_managed_vms()
    return jsonify({"vms": vms}), 200


def start_api_server(config: dict) -> None:
    """Start the agent API server."""
    port = config["listen_port"]
    tls_cert = config.get("tls_cert")
    tls_key = config.get("tls_key")

    ssl_context = None
    if tls_cert and tls_key:
        ssl_context = (tls_cert, tls_key)

    logger.info("Agent API listening on port %d", port)
    app.run(host="0.0.0.0", port=port, ssl_context=ssl_context)
