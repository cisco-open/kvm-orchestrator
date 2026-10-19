"""Flask extensions — initialised in create_app()."""

from celery import Celery
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# SQLAlchemy 2.x declarative base
class Base(DeclarativeBase):
    pass


# These are initialised later in create_app()
engine = None
SessionLocal: sessionmaker[Session] | None = None
jwt = JWTManager()
cors = CORS()
celery = Celery(__name__)


def init_db(app):
    """Create engine and session factory from app config."""
    global engine, SessionLocal
    engine = create_engine(
        app.config["SQLALCHEMY_DATABASE_URI"],
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_celery(app):
    """Configure Celery from Flask app config."""
    celery.conf.broker_url = app.config["CELERY_BROKER_URL"]
    celery.conf.result_backend = app.config["CELERY_RESULT_BACKEND"]
    celery.conf.task_serializer = "json"
    celery.conf.result_serializer = "json"
    celery.conf.accept_content = ["json"]
    celery.conf.timezone = "UTC"
    celery.conf.beat_schedule = {
        "check-node-heartbeats": {
            "task": "app.tasks.heartbeat.check_node_heartbeats",
            "schedule": 15.0,
        },
        "sync-ad-groups": {
            "task": "app.tasks.group_sync.sync_all_groups",
            "schedule": 3600.0,  # Every hour
        },
        "check-vm-expiry": {
            "task": "app.tasks.vm_expiry.check_vm_expiry",
            # Every 6h so a test environment (short TTLs) exercises the machine
            # promptly; harmless in prod where terms are a year. The task is a
            # no-op unless LIFECYCLE_ENABLED=true.
            "schedule": 21600.0,
        },
        "discover-networks": {
            "task": "app.tasks.network_discovery.discover_networks",
            "schedule": 3600.0,  # Every hour — pick up newly-added VLANs
        },
    }

    class ContextTask(celery.Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask
