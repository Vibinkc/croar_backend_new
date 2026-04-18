import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Boolean, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text

from .base import EnterpriseBase


class EmailTemplate(EnterpriseBase):
    __tablename__ = "email_templates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(50), default="GENERAL", server_default="GENERAL")
    variables: Mapped[list[str] | None] = mapped_column(JSONB, default=[])  # List of variable names

    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )

    logs = relationship("EmailLog", back_populates="template")


class EmailDirection:
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"


class EmailLog(EnterpriseBase):
    __tablename__ = "email_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )

    direction: Mapped[str] = mapped_column(String(20), default="OUTBOUND")  # INBOUND or OUTBOUND
    sender_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recipient_email: Mapped[str] = mapped_column(String(255), nullable=False)

    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending")  # pending, sent, failed, received
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    sent_at: Mapped[datetime] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    message_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )  # SMTP Message-ID for threading

    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("email_templates.id", ondelete="SET NULL"), nullable=True
    )
    template = relationship("EmailTemplate", back_populates="logs")

    candidate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    application_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    automation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mail_automations.id", ondelete="SET NULL"), nullable=True
    )

    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )


class MailAutomation(EnterpriseBase):
    """Stores conditional email automation rules per job and hiring round/stage."""

    __tablename__ = "mail_automations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )

    job_requirement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_requirements.id", ondelete="CASCADE"), nullable=False
    )
    stage_index: Mapped[int] = mapped_column(Integer, nullable=False, default=1)  # 1-based hiring round
    stage_name: Mapped[str | None] = mapped_column(String(255), nullable=True)  # display label

    criteria: Mapped[str] = mapped_column(Text, nullable=False)  # free-text condition e.g. "AI score > 80"

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("email_templates.id", ondelete="CASCADE"), nullable=False
    )
    auto_move: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    is_immediate: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    send_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)

    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )

    job = relationship("JobRequirement")
    template = relationship("EmailTemplate")
