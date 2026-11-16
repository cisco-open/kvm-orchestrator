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

# docker build -t kvm-orchestrator/ui:latest .
# v0.24.0 — Frontend UX polish: after login users land on /vms (Virtual Machines) instead of /dashboard; Host and Datastore columns on the VMs page are now admin-only (they leak infra topology non-admins do not need).
# v0.23.0 — Version bump only; no frontend code changes (backend-only MCP impersonation release).

# Stage 1: Build
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
RUN npm run build

# Stage 2: Serve
FROM nginx:alpine
LABEL org.opencontainers.image.version="v0.35.7" \
      org.opencontainers.image.description="vm_sync single-flight per node (Redis lock) + touch-only unchanged VMs (no lock waits / binlog flood); readable event logging with user, real client IP (TRUSTED_PROXIES) and request ID, nginx real_ip + X-Request-ID + clean access log, console open/close lines; MySQL binlog off by default (helm mysql.disableBinlog); node detail page uses the shared VM table."
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
# Default trusted proxies for real_ip; regenerated from TRUSTED_PROXIES at start.
RUN printf 'set_real_ip_from 10.42.0.0/16;\nset_real_ip_from 127.0.0.1;\n' > /etc/nginx/conf.d/00-real-ip.conf
COPY docker-entrypoint.sh /docker-entrypoint-wvc.sh
RUN chmod +x /docker-entrypoint-wvc.sh
EXPOSE 80
# Entrypoint injects /env.js from the ENVIRONMENT var, then execs nginx.
CMD ["/docker-entrypoint-wvc.sh"]
