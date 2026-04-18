import uuid
from datetime import date, datetime

from sqlalchemy import ARRAY, TIMESTAMP, Boolean, Date, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text

from .base import EnterpriseBase

# Enums
INTERVIEW_TYPE = ENUM("HR", "TECHNICAL", "VIDEO", "LIVE", name="interview_type", create_type=True)


class Interview(EnterpriseBase):
    __tablename__ = "interviews"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    topic: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duration: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    difficulty: Mapped[str] = mapped_column(String(50), default="Intermediate")
    require_video: Mapped[bool] = mapped_column(Boolean, default=True)

    type: Mapped[str] = mapped_column(INTERVIEW_TYPE, default="VIDEO")

    plan: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    avatar_config: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    settings: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    company_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)


class InterviewAutomation(EnterpriseBase):
    __tablename__ = "interview_automations"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    job_requirement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_requirements.id", ondelete="CASCADE"), nullable=False
    )
    stage_index: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    stage_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    criteria: Mapped[str] = mapped_column(String(255), nullable=False)

    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    start_time: Mapped[str] = mapped_column(String(10), default="09:00")
    end_time: Mapped[str] = mapped_column(String(10), default="17:00")
    duration: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    daily_limit: Mapped[int] = mapped_column(Integer, default=5)

    interviewer_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    time_slots: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    google_meet_link: Mapped[str | None] = mapped_column(String, nullable=True)

    interview_type: Mapped[str] = mapped_column(String(50), default="GMEET", server_default="GMEET")
    interview_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interviews.id", ondelete="SET NULL"), nullable=True
    )

    email_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("email_templates.id", ondelete="SET NULL"), nullable=True
    )

    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_move: Mapped[bool] = mapped_column(Boolean, default=False)

    company_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())

    job = relationship("JobRequirement")
    email_template = relationship("EmailTemplate")
    interview_template = relationship("Interview")


class InterviewSchedule(EnterpriseBase):
    __tablename__ = "interview_schedules"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )

    interview_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interviews.id", ondelete="CASCADE"), nullable=True
    )
    automation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_automations.id", ondelete="CASCADE"), nullable=True
    )
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidate_applications.id", ondelete="CASCADE"), nullable=True
    )
    interviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    scheduled_time: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False)
    meeting_link: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="SCHEDULED")

    company_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )

    interview = relationship("Interview", backref="schedules")
    automation = relationship("InterviewAutomation", backref="schedules")
    application = relationship("CandidateApplication", back_populates="interview_schedules")
    interviewer = relationship("EnterpriseUser")
    attempts = relationship("InterviewAttempt", back_populates="schedule", cascade="all, delete-orphan")


class InterviewAttempt(EnterpriseBase):
    __tablename__ = "interview_attempts"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )

    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_schedules.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    transcript: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    ai_feedback: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    overall_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)

    company_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )

    schedule = relationship("InterviewSchedule", back_populates="attempts")
    user = relationship("EnterpriseUser")
