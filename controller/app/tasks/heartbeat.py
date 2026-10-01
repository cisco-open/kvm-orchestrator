"""Heartbeat monitoring task (Celery Beat)."""

from ..extensions import celery


@celery.task(name="app.tasks.heartbeat.check_node_heartbeats")
def check_node_heartbeats():
    """
    Periodic task: check all HA-enabled nodes for stale heartbeats.
    Runs every 15 seconds via Celery Beat.
    """
    from ..services.ha_engine import check_node_heartbeats as _check

    _check()
