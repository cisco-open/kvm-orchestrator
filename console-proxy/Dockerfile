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
