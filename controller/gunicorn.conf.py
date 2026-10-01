"""Gunicorn configuration — enables Prometheus multiprocess mode.

Two responsibilities:

1. **Guarantee `PROMETHEUS_MULTIPROC_DIR` is set** before workers fork.
   `prometheus_client` reads this env var at counter construction time; if
   it's unset when the first Counter/Histogram is created, the metric is
   permanently pinned to in-memory storage and no amount of later env-var
   setting will fix it. The Dockerfile sets this via ENV as the primary
   mechanism; the `setdefault` below is a safety net for out-of-container
   `gunicorn -c gunicorn.conf.py wsgi:app` invocations.

2. **Wipe stale shard files on boot, reap dead-worker shards on exit.**
   Without `on_starting`, a container restart would keep the old shard
   files and inflate counter values on the first scrape. Without
   `child_exit`, a worker that crashes and gets replaced leaves its shards
   behind forever, so every scrape aggregates stale data indefinitely.

Reference: https://prometheus.github.io/client_python/multiprocess/
"""

import os
import shutil


# Ensure PROMETHEUS_MULTIPROC_DIR is defined *before* the app is imported.
# gunicorn reads this file before it imports wsgi:app, so any setdefault
# here is guaranteed to run before app.metrics defines its Counter objects.
os.environ.setdefault("PROMETHEUS_MULTIPROC_DIR", "/tmp/prometheus_multiproc")


def on_starting(server):
    """Wipe & recreate the multiproc shard directory when gunicorn boots."""
    d = os.environ["PROMETHEUS_MULTIPROC_DIR"]
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    server.log.info("Prometheus multiproc dir wiped and recreated: %s", d)


def child_exit(server, worker):
    """Reap the exiting worker's shard files so scrapes stop aggregating them."""
    from prometheus_client import multiprocess
    multiprocess.mark_process_dead(worker.pid)
