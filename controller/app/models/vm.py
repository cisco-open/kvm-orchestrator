"""Virtual Machine model."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import Base


class VM(Base):
    __tablename__ = "vms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    node_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("nodes.id"), nullable=False
    )
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False
    )
    group_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("groups.id"), nullable=True
    )

    # State
    state: Mapped[str] = mapped_column(
        String(20), default="stopped"
    )  # running/stopped/paused/error/migrating

    # Resources
    vcpus: Mapped[int] = mapped_column(Integer, nullable=False)
    ram_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    disk_gb: Mapped[int] = mapped_column(Integer, nullable=False)

    # Storage
    ceph_pool: Mapped[str] = mapped_column(String(100), default="libvirt-pool")
    ceph_image: Mapped[str] = mapped_column(String(255), nullable=False)

    # Networking
    networks: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Libvirt XML
    xml_definition: Mapped[str | None] = mapped_column(Text, nullable=True)

    # HA
    ha_restart: Mapped[bool] = mapped_column(Boolean, default=True)
    ha_priority: Mapped[int] = mapped_column(Integer, default=100)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
