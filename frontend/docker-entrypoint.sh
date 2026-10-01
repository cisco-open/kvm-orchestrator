#!/bin/sh
# Runtime env injection for the UI. Writes /env.js (served by nginx, loaded by
# index.html + console.html before the bundle) from the ENVIRONMENT and BRANDING
# pod env vars so the SAME image renders as:
#   - the default open-source KVMaestro build, or your own white-labelled
#     build (BRANDING), and
#   - live (blue) or test (red + warning banner) (ENVIRONMENT),
# purely by namespace config — no separate image, no rebuild to rebrand/retheme.
set -e

ENVIRONMENT="${ENVIRONMENT:-live}"
BRANDING="${BRANDING:-kvmaestro}"
ENV_JS="/usr/share/nginx/html/env.js"

cat > "$ENV_JS" <<EOF
window.__ENV__ = { ENVIRONMENT: "${ENVIRONMENT}", BRANDING: "${BRANDING}" };
EOF

echo "wvc-ui: wrote ${ENV_JS} (ENVIRONMENT=${ENVIRONMENT} BRANDING=${BRANDING})"

exec nginx -g 'daemon off;'
