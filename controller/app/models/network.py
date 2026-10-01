"""Network model (v0.15.0).

The hand-written migration `v0.15.0_networks.sql` is the source of truth
for the table; this model exists so Alembic autogenerate (if it's ever
re-enabled) sees the same shape. Service code uses raw pymysql via
`services/network_service.py`, not this model.

Schema change at v0.15.0: dropped the unused `subnet` (VARCHAR) and
`available_on_nodes` (JSON) columns; added `user_visible` (TINYINT) and
`is_default` (TINYINT). See the migration for the full rationale.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import Base


class Network(Base):
    __tablename__ = "networks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    bridge_name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    vlan_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
