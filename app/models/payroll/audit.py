import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.enterprise.base import EnterpriseBase as Base


class AuditLog(Base):
    """Append-only activity trail: who did what, when (spec gap — operational
    polish). One row per authenticated mutating request, written by the audit
    middleware. Deliberately decoupled (no FKs / soft-delete) so the trail
    survives deletion of the entities it references and is never mutated."""

    # Renamed from "audit_logs" on integration to avoid colliding with Croar's
    # own shared AuditLog table; the payroll activity trail is kept separate.
    __tablename__ = "payroll_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    # No FK: the log is immutable history, kept even if company/user is removed.
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(160))
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(255))
    status_code: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
