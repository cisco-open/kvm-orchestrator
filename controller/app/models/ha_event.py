"""HA Event tracking model."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import Base


class HAEvent(Base):
    __tablename__ = "ha_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    node_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("nodes.id"), nullable=True
    )
    vm_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("vms.id"), nullable=True)
    source_node_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_node_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trigger: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # heartbeat_timeout/manual
    result: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # success/failed
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
