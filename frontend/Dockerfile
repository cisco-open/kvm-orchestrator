# docker build -t kvmaestro/ui:latest .
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
LABEL org.opencontainers.image.version="v0.34.0" \
      org.opencontainers.image.description="frontend: runtime BRANDING toggle (kvmaestro debranded default / kvmaestro internal) via env.js; brand-driven name, logo, favicon, title; test-env red overlay kept independent."
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY docker-entrypoint.sh /docker-entrypoint-wvc.sh
RUN chmod +x /docker-entrypoint-wvc.sh
EXPOSE 80
# Entrypoint injects /env.js from the ENVIRONMENT var, then execs nginx.
CMD ["/docker-entrypoint-wvc.sh"]
