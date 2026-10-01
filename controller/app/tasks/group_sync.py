"""AD group sync task (Celery Beat)."""

from ..extensions import celery


@celery.task(name="app.tasks.group_sync.sync_all_groups")
def sync_all_groups():
    """Periodic task: sync all active users' AD group memberships."""
    from ..services.group_service import sync_all_groups as _sync

    _sync()
