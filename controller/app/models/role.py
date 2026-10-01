"""Role, Permission, and UserRole models."""

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import Base


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("roles.id"), nullable=False
    )
    resource_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # node/vm/network/user/system
    resource_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actions: Mapped[list] = mapped_column(JSON, nullable=False)


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), primary_key=True
    )
    role_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("roles.id"), primary_key=True
    )
    scope_type: Mapped[str] = mapped_column(
        String(10), default="global"
    )  # global/group/node
    scope_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
