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

"""Logging setup (v0.35.7) — one human-readable style everywhere.

    [2026-11-16 10:41:02,113] INFO     kvm-orchestrator :: alice started VM web-01 on node-1 from 203.0.113.10 [3f9c…]

One stdout handler on the root logger; gunicorn's loggers and every ``wvc.*``
logger propagate to it, so the API, celery worker and beat all look alike.
Level from ``LOG_LEVEL`` (DEBUG/INFO/WARNING/ERROR, default INFO). Periodic
chatter and per-request access lines are DEBUG; user/engine events are INFO+.
"""

from __future__ import annotations

import logging
import os
import sys

LOG_FORMAT = "[%(asctime)s] %(levelname)-8s kvm-orchestrator :: %(message)s"

_LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARN": logging.WARNING,
           "WARNING": logging.WARNING, "ERROR": logging.ERROR}


def log_level() -> int:
    return _LEVELS.get(os.environ.get("LOG_LEVEL", "INFO").strip().upper(), logging.INFO)


def configure() -> None:
    """Idempotent: install a single formatted stdout handler on root."""
    level = log_level()
    root = logging.getLogger()
    handler = next((h for h in root.handlers if getattr(h, "_wvc", False)), None)
    if handler is None:
        handler = logging.StreamHandler(sys.stdout)
        handler._wvc = True  # type: ignore[attr-defined]
        root.handlers = [handler]
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.setLevel(level)

    for name in ("gunicorn.error", "wvc", "celery", "celery.task"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
        lg.setLevel(level)
    # gunicorn's per-request access lines (incl. kubelet /healthz probes and
    # Prometheus /metrics scrapes) only at DEBUG; events.py covers real actions.
    acc = logging.getLogger("gunicorn.access")
    acc.handlers = []
    acc.propagate = True
    acc.setLevel(logging.DEBUG if level <= logging.DEBUG else logging.WARNING)
    # Third-party noise stays quiet unless explicitly debugging.
    for name in ("paramiko", "urllib3", "werkzeug", "kombu", "amqp",
                 "celery.app.trace", "celery.beat", "celery.worker.strategy"):
        logging.getLogger(name).setLevel(max(level, logging.WARNING))
