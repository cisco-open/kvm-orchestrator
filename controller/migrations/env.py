"""Alembic env.py for database migrations.

Kept intentionally Flask-free: reads DATABASE_URL from the environment
directly rather than importing app.config, so migrations can run in a
minimal container (alembic + sqlalchemy + pymysql) without pulling in
celery, redis, ldap3, prometheus_client, paramiko, etc. as transitive
imports of `app/__init__.py`.

target_metadata is None on purpose. Migrations are hand-written raw MySQL
via `op.execute()` — see 0001_baseline_v0_17_0.py for the rationale
(the service layer uses raw pymysql, so the SQLAlchemy models drift from
the real schema and are not a reliable source of truth for autogenerate).
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "mysql+pymysql://root:root@localhost:3306/kvm-orchestrator"
)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in offline mode."""
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in online mode."""
    configuration = {"sqlalchemy.url": DATABASE_URL}
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
