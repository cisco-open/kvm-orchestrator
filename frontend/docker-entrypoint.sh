#!/bin/sh
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

# Runtime env injection for the UI. Writes /env.js (served by nginx, loaded by
# index.html + console.html before the bundle) from the ENVIRONMENT and BRANDING
# pod env vars so the SAME image renders as:
#   - the default open-source KVM Orchestrator build, or your own white-labelled
#     build (BRANDING), and
#   - live (blue) or test (red + warning banner) (ENVIRONMENT),
# purely by namespace config — no separate image, no rebuild to rebrand/retheme.
set -e

ENVIRONMENT="${ENVIRONMENT:-live}"
BRANDING="${BRANDING:-kvm-orchestrator}"
ENV_JS="/usr/share/nginx/html/env.js"

cat > "$ENV_JS" <<EOF
window.__ENV__ = { ENVIRONMENT: "${ENVIRONMENT}", BRANDING: "${BRANDING}" };
EOF

echo "wvc-ui: wrote ${ENV_JS} (ENVIRONMENT=${ENVIRONMENT} BRANDING=${BRANDING})"

exec nginx -g 'daemon off;'
