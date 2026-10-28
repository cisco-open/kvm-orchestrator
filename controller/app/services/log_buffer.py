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

"""In-memory ring buffer log handler + accessor.

Captures recent `wvc.*` log records in a bounded deque so admins can view
recent application logs in the UI without trawling `kubectl logs`. This is
per-process (each gunicorn worker/celery worker has its own buffer) — it is a
convenience view of RECENT activity, not a durable log store. For full history
use the pod logs / a log aggregator.
"""

import logging
import threading
from collections import deque
from datetime import datetime, timezone

_MAX_RECORDS = 2000
_buffer: "deque[dict]" = deque(maxlen=_MAX_RECORDS)
_lock = threading.Lock()
_seq = 0


class RingBufferHandler(logging.Handler):
    """Logging handler that appends structured records to the ring buffer."""

    def emit(self, record: logging.LogRecord) -> None:
        global _seq
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        entry = {
            "ts": datetime.fromtimestamp(
                record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": msg,
        }
        if record.exc_info:
            try:
                entry["exc"] = self.format(record)
            except Exception:
                pass
        with _lock:
            global _seq  # noqa: F824
            _seq += 1
            entry["seq"] = _seq
            _buffer.append(entry)


def install(level: int = logging.INFO, logger_names=("wvc",)) -> None:
    """Attach the ring-buffer handler to the given logger namespaces."""
    handler = RingBufferHandler()
    handler.setLevel(level)
    for name in logger_names:
        lg = logging.getLogger(name)
        # Avoid double-install.
        if not any(isinstance(h, RingBufferHandler) for h in lg.handlers):
            lg.addHandler(handler)


def get_records(limit: int = 500, level: str | None = None,
                contains: str | None = None) -> list[dict]:
    """Return recent buffered records, newest first, optionally filtered."""
    with _lock:
        items = list(_buffer)
    if level:
        wanted = level.upper()
        order = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
        floor = order.get(wanted, 0)
        items = [r for r in items if order.get(r["level"], 0) >= floor]
    if contains:
        c = contains.lower()
        items = [r for r in items if c in r["message"].lower()
                 or c in r["logger"].lower()]
    items = items[-limit:]
    items.reverse()  # newest first
    return items
