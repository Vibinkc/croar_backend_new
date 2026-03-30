import uuid
from typing import Optional, List
from sqlalchemy import String, TIMESTAMP, func, ForeignKey, Text, Boolean, Integer
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text
from . import EnterpriseBase

class EmailTemplate(EnterpriseBase):
    __tablename__ = "email_templates"
    
    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()"))
    
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[Text] = mapped_column(Text, nullable=False)
    variables: Mapped[Optional[List[str]]] = mapped_column(JSONB, default=[]) # List of variable names
    
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now())
    
    logs = relationship("EmailLog", back_populates="template")

class EmailDirection:
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"

class EmailLog(EnterpriseBase):
    __tablename__ = "email_logs"
    
    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()"))
    
    direction: Mapped[str] = mapped_column(String(20), default="OUTBOUND") # INBOUND or OUTBOUND
    sender_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    recipient_email: Mapped[str] = mapped_column(String(255), nullable=False)
    
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[Text] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending") # pending, sent, failed, received
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    sent_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    message_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True) # SMTP Message-ID for threading
    
    template_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=True), ForeignKey("email_templates.id", ondelete="SET NULL"), nullable=True)
    template = relationship("EmailTemplate", back_populates="logs")
    
    candidate_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=True), nullable=True)
    application_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=True), nullable=True)
    automation_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=True), ForeignKey("mail_automations.id", ondelete="SET NULL"), nullable=True)


class MailAutomation(EnterpriseBase):
    """Stores conditional email automation rules per job and hiring round/stage."""
    __tablename__ = "mail_automations"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()"))

    job_requirement_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("job_requirements.id", ondelete="CASCADE"), nullable=False)
    stage_index: Mapped[int] = mapped_column(Integer, nullable=False, default=1)  # 1-based hiring round
    stage_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # display label

    criteria: Mapped[str] = mapped_column(Text, nullable=False)  # free-text condition e.g. "AI score > 80"

    template_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("email_templates.id", ondelete="CASCADE"), nullable=False)
    auto_move: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    is_immediate: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    send_at: Mapped[Optional[TIMESTAMP]] = mapped_column(TIMESTAMP, nullable=True)

    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now())

    job = relationship("JobRequirement")
    template = relationship("EmailTemplate")

