import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class AssessmentType(enum.StrEnum):
    APTITUDE = "APTITUDE"
    CODING = "CODING"
    BOTH = "BOTH"


class AssessmentTemplate(Base):
    __tablename__ = "assessment_templates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)

    type: Mapped[AssessmentType] = mapped_column(
        SqlEnum(AssessmentType), nullable=False, default=AssessmentType.APTITUDE
    )
    topic: Mapped[str] = mapped_column(String, nullable=False)
    question_count: Mapped[int] = mapped_column(Integer, default=10)
    generated_questions: Mapped[list[dict[str, object]] | None] = mapped_column(
        JSONB, nullable=True
    )  # List of questions
    test_duration: Mapped[int] = mapped_column(Integer, nullable=False, default=30)  # minutes
    email_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("email_templates.id", ondelete="SET NULL"), nullable=True
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    email_template = relationship("EmailTemplate")


class AssessmentAutomation(Base):
    __tablename__ = "assessment_automations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_requirement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_requirements.id", ondelete="CASCADE"), nullable=False
    )
    stage_index: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    stage_name: Mapped[str | None] = mapped_column(String, nullable=True)
    criteria: Mapped[str] = mapped_column(String, nullable=False)

    type: Mapped[AssessmentType] = mapped_column(
        SqlEnum(AssessmentType), nullable=False, default=AssessmentType.APTITUDE
    )
    topic: Mapped[str] = mapped_column(String, nullable=False)
    question_count: Mapped[int] = mapped_column(Integer, default=10)
    generated_questions: Mapped[list[dict[str, object]] | None] = mapped_column(
        JSONB, nullable=True
    )  # List of questions
    test_duration: Mapped[int] = mapped_column(Integer, nullable=False, default=30)  # minutes
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assessment_templates.id", ondelete="SET NULL"), nullable=True
    )
    email_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("email_templates.id", ondelete="SET NULL"), nullable=True
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )

    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_immediate: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_move: Mapped[bool] = mapped_column(Boolean, default=False)
    send_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    job = relationship("JobRequirement", back_populates="assessment_automations")
    attempts = relationship("AssessmentAttempt", back_populates="automation", cascade="all, delete-orphan")
    template = relationship("AssessmentTemplate")
    email_template = relationship("EmailTemplate")


class AssessmentAttempt(Base):
    __tablename__ = "assessment_attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    automation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assessment_automations.id", ondelete="CASCADE"), nullable=True
    )
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assessment_templates.id", ondelete="SET NULL"), nullable=True
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidate_applications.id", ondelete="CASCADE"), nullable=False
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )

    answers: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)  # Candidate's responses
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Overall score
    aptitude_score: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Separate score for aptitude
    coding_score: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Separate score for coding
    status: Mapped[str] = mapped_column(String, default="STARTED")  # STARTED, COMPLETED, EXPIRED

    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    automation = relationship("AssessmentAutomation", back_populates="attempts")
    template = relationship("AssessmentTemplate")
    candidate = relationship("Candidate")
    application = relationship("CandidateApplication")
