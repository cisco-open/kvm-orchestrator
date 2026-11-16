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

"""Unit tests for app.events (client IP, event lines, header hygiene).

Run: cd controller && python -m pytest -q tests/test_events.py
"""

import logging

import pytest
from flask import Blueprint, Flask, jsonify
from flask_jwt_extended import JWTManager, create_access_token, jwt_required

from app import events
from app.logsetup import LOG_FORMAT


@pytest.fixture()
def app(monkeypatch):
    names = {("users", 7): "alice", ("vms", 12): "web-01"}
    monkeypatch.setattr(events, "_lookup", lambda t, c, i: names.get((t, int(i))) if i is not None else None)
    a = Flask("t")
    a.config.update(JWT_SECRET_KEY="unit-test-only-key-0123456789abcdef")
    JWTManager(a)
    events.install(a)

    bp = Blueprint("vms", __name__)

    @bp.post("/<int:vm_id>/start")
    @jwt_required()
    def start_vm(vm_id):
        return jsonify(ok=True)

    @bp.post("/<int:vm_id>/reboot")
    @jwt_required()
    def reboot_vm(vm_id):
        return jsonify(error="node unreachable"), 400

    @bp.get("")
    def list_vms():
        return jsonify([])

    a.register_blueprint(bp, url_prefix="/api/vms")

    @a.get("/healthz")
    def healthz():
        return "ok"

    return a


@pytest.mark.parametrize("xff,peer,expected", [
    ("203.0.113.10", "10.42.1.1", "203.0.113.10"),                    # Traefik -> nginx -> api
    ("203.0.113.10, 10.42.62.64", "10.42.1.1", "203.0.113.10"),       # extra internal hop
    ("198.51.100.66, 203.0.113.10, 10.42.0.5", "10.42.1.1", "203.0.113.10"),  # spoofed left-most ignored
    ("", "10.42.1.1", "10.42.1.1"),                                   # in-cluster caller
    ("192.0.2.4", "192.0.2.8", "192.0.2.8"),                                # untrusted peer: XFF ignored
])
def test_client_ip(xff, peer, expected):
    a = Flask("ip")
    headers = {"X-Forwarded-For": xff} if xff else {}
    with a.test_request_context("/", headers=headers, environ_base={"REMOTE_ADDR": peer}):
        assert events.client_ip() == expected


def _call(app, caplog, method, path, rid="rid-123", auth=True):
    with app.app_context():
        tok = create_access_token(identity="7")
    headers = {"X-Forwarded-For": "203.0.113.10", "X-Request-ID": rid}
    if auth:
        headers["Authorization"] = f"Bearer {tok}"
    with caplog.at_level(logging.DEBUG):
        resp = app.test_client().open(path, method=method, headers=headers,
                                      environ_base={"REMOTE_ADDR": "10.42.9.9"})
    return resp, tok


def test_event_line_success(app, caplog):
    resp, tok = _call(app, caplog, "POST", "/api/vms/12/start")
    lines = [r.getMessage() for r in caplog.records if r.name == "wvc.events"]
    assert lines == ["alice started VM web-01 from 203.0.113.10 [rid-123]"]
    assert resp.headers["X-Request-ID"] == "rid-123"
    assert events.USER_HEADER not in resp.headers  # not via UI nginx -> never exposed
    assert all(tok not in r.getMessage() for r in caplog.records)


def test_event_line_failure(app, caplog):
    _call(app, caplog, "POST", "/api/vms/12/reboot")
    rec = [r for r in caplog.records if r.name == "wvc.events"][0]
    assert rec.levelno == logging.WARNING
    assert rec.getMessage() == ("alice failed to reboot VM web-01 from 203.0.113.10: "
                                "node unreachable (HTTP 400) [rid-123]")


def test_get_and_probes_quiet(app, caplog):
    _call(app, caplog, "GET", "/api/vms")
    _call(app, caplog, "GET", "/healthz", auth=False)
    assert not [r for r in caplog.records if r.levelno >= logging.INFO]


def test_bad_request_id_replaced(app, caplog):
    resp, _ = _call(app, caplog, "GET", "/api/vms", rid='x" injected')
    assert resp.headers["X-Request-ID"] != 'x" injected'


def test_format_matches_self_service():
    assert LOG_FORMAT == "[%(asctime)s] %(levelname)-8s kvm-orchestrator :: %(message)s"


def test_user_header_only_via_ui_nginx(app):
    with app.app_context():
        tok = create_access_token(identity="7")
    resp = app.test_client().post("/api/vms/12/start", headers={
        "Authorization": f"Bearer {tok}", "X-WVC-Via": "ui-nginx"},
        environ_base={"REMOTE_ADDR": "10.42.3.3"})
    assert resp.headers[events.USER_HEADER] == "alice"


def test_trusted_proxies_parse():
    nets = events.parse_trusted_proxies("10.244.0.0/16, 127.0.0.1,bogus,,")
    assert [str(n) for n in nets] == ["10.244.0.0/16", "127.0.0.1/32"]
    assert [str(n) for n in events.parse_trusted_proxies(None)] == ["10.42.0.0/16", "127.0.0.1/32"]
