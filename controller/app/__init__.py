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

"""Flask application factory."""

import logging
import os

from flask import Flask

from .config import config_by_name
from .extensions import celery, cors, init_celery, init_db, jwt
from .version import VERSION, RELEASE_NOTE

logger = logging.getLogger("wvc.app")


def create_app(config_name: str | None = None) -> Flask:
    """Create and configure the Flask application."""
    if config_name is None:
        config_name = os.environ.get("FLASK_ENV", "development")

    # Route all wvc.* loggers through gunicorn's error log handler so that
    # vm_service, node_monitor, vm_sync etc. all appear in docker logs.
    gunicorn_logger = logging.getLogger("gunicorn.error")
    logging.getLogger("wvc").handlers = gunicorn_logger.handlers
    logging.getLogger("wvc").setLevel(gunicorn_logger.level or logging.INFO)

    # Also capture recent wvc.* logs into an in-memory ring buffer so admins can
    # view them in the UI (Admin → Logs) without trawling `kubectl logs`.
    try:
        from .services import log_buffer
        log_buffer.install(level=logging.INFO)
    except Exception:
        logging.getLogger("wvc").warning("log ring-buffer install failed")

    gunicorn_logger.info("kvm-orchestrator-api %s — %s", VERSION, RELEASE_NOTE)

    app = Flask(__name__)
    app.config.from_object(config_by_name[config_name])

    # Initialise extensions
    init_db(app)
    init_celery(app)
    jwt.init_app(app)
    cors.init_app(app, resources={r"/api/*": {"origins": "*"}})

    # Install Prometheus HTTP RED middleware + publish build version.
    # Must run before blueprints are registered so before_request/after_request
    # hooks are in place when the first request lands.
    from .metrics import install as install_metrics, set_build_info
    install_metrics(app)
    set_build_info(VERSION)

    # Register API blueprints
    from .api import register_blueprints

    register_blueprints(app)

    # Register error handlers
    from .utils.errors import register_error_handlers

    register_error_handlers(app)

    # Start background node monitor (SSH-polls each registered node)
    from .services.node_monitor import start_monitor
    start_monitor()

    # Start background VM inventory sync
    from .services.vm_sync import start_vm_sync
    start_vm_sync()

    return app
