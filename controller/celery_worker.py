"""Celery worker entry point."""

from app import create_app
from app.extensions import celery

# Import tasks so they get registered
import app.tasks.heartbeat  # noqa: F401
import app.tasks.ha_relocate  # noqa: F401
import app.tasks.group_sync  # noqa: F401
import app.tasks.vm_ops  # noqa: F401
import app.tasks.vm_expiry  # noqa: F401
import app.tasks.network_discovery  # noqa: F401

flask_app = create_app()
flask_app.app_context().push()
