import uuid
from typing import cast

from sqlalchemy import TIMESTAMP, Boolean, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text

from app.models.shared.auth import user_roles

from .base import EnterpriseBase


class EnterpriseUser(EnterpriseBase):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    profile_image: Mapped[str | None] = mapped_column(String(255), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_self_registered: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # role_id is deprecated in favor of many-to-many 'roles' relationship
    role_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id"), nullable=True
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)

    # Relationships
    roles = relationship("Role", secondary=user_roles, back_populates="users")
    company = relationship("Company")

    @property
    def full_name(self) -> str:
        """Display name for payroll/self-service (Croar stores first/last separately)."""
        name = f"{self.first_name or ''} {self.last_name or ''}".strip()
        return name or self.email

    @property
    def role(self) -> object | None:
        """Compatibility property for legacy code expecting a single role."""
        if self.roles:
            return cast("object", self.roles[0])
        return None
