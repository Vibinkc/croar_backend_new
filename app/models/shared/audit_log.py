from sqlalchemy import TIMESTAMP, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text

from .base import SharedBase


class AuditLog(SharedBase):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    admin_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("super_admins.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=True)  # Target entity ID
    details: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    timestamp: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, server_default=func.now(), index=True)

    admin = relationship("SuperAdmin")
