"""Node (hypervisor host) model."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import Base


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hostname: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    ip_address: Mapped[str] = mapped_column(String(45), nullable=False)
    port: Mapped[int] = mapped_column(Integer, default=8444)
    status: Mapped[str] = mapped_column(
        String(20), default="offline"
    )  # online/offline/maintenance/fenced

    # Capacity
    total_vcpus: Mapped[int] = mapped_column(Integer, default=0)
    total_ram_mb: Mapped[int] = mapped_column(Integer, default=0)
    total_disk_gb: Mapped[int] = mapped_column(Integer, default=0)
    available_vcpus: Mapped[int] = mapped_column(Integer, default=0)
    available_ram_mb: Mapped[int] = mapped_column(Integer, default=0)
    available_disk_gb: Mapped[int] = mapped_column(Integer, default=0)

    # Agent info
    agent_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    # HA
    ha_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    ha_priority: Mapped[int] = mapped_column(Integer, default=100)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
