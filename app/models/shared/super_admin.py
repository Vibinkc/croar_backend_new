from typing import cast

from sqlalchemy import TIMESTAMP, Boolean, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text

from .base import SharedBase


class SuperAdmin(SharedBase):
    __tablename__ = "super_admins"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    # Relationship to Roles (Multi-Role Support)
    roles = relationship("Role", secondary="super_admin_roles", back_populates="super_admins")

    @property
    def role(self) -> str:
        if self.roles:
            return cast("str", self.roles[0].name)
        return "SUPER_ADMIN"

    @property
    def password(self) -> str:
        return self.password_hash
