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

# docker build -t kvm-orchestrator/console-proxy:latest .
# v0.24.0 — Version bump only; no console-proxy code changes (aligns image labels with fleet v0.24.0).
# v0.23.0 — Version bump only; no console-proxy code changes (backend-only MCP impersonation release).

FROM python:3.11-slim

LABEL org.opencontainers.image.version="v0.35.1" \
      org.opencontainers.image.description="console-proxy unchanged; version bump to keep image labels aligned with fleet v0.32.0."

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY proxy/ ./proxy/

EXPOSE 8765
CMD ["python", "-m", "proxy.main"]
