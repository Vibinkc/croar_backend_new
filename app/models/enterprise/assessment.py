import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


class AssessmentType(str, enum.Enum):
    APTITUDE = "APTITUDE"
    CODING = "CODING"
    BOTH = "BOTH"


class AssessmentTemplate(Base):
    __tablename__ = "assessment_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)

    type = Column(SqlEnum(AssessmentType), nullable=False, default=AssessmentType.APTITUDE)
    topic = Column(String, nullable=False)
    question_count = Column(Integer, default=10)
    generated_questions = Column(JSONB, nullable=True)  # List of questions
    test_duration = Column(Integer, nullable=False, default=30)  # minutes
    email_template_id = Column(
        UUID(as_uuid=True), ForeignKey("email_templates.id", ondelete="SET NULL"), nullable=True
    )
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    email_template = relationship("EmailTemplate")


class AssessmentAutomation(Base):
    __tablename__ = "assessment_automations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_requirement_id = Column(
        UUID(as_uuid=True), ForeignKey("job_requirements.id", ondelete="CASCADE"), nullable=False
    )
    stage_index = Column(Integer, nullable=False, default=1)
    stage_name = Column(String, nullable=True)
    criteria = Column(String, nullable=False)

    type = Column(SqlEnum(AssessmentType), nullable=False, default=AssessmentType.APTITUDE)
    topic = Column(String, nullable=False)
    question_count = Column(Integer, default=10)
    generated_questions = Column(JSONB, nullable=True)  # List of questions
    test_duration = Column(Integer, nullable=False, default=30)  # minutes
    template_id = Column(
        UUID(as_uuid=True), ForeignKey("assessment_templates.id", ondelete="SET NULL"), nullable=True
    )
    email_template_id = Column(
        UUID(as_uuid=True), ForeignKey("email_templates.id", ondelete="SET NULL"), nullable=True
    )
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True)

    is_enabled = Column(Boolean, default=True)
    is_immediate = Column(Boolean, default=True)
    auto_move = Column(Boolean, default=False)
    send_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    job = relationship("JobRequirement", back_populates="assessment_automations")
    attempts = relationship("AssessmentAttempt", back_populates="automation", cascade="all, delete-orphan")
    template = relationship("AssessmentTemplate")
    email_template = relationship("EmailTemplate")


class AssessmentAttempt(Base):
    __tablename__ = "assessment_attempts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    automation_id = Column(
        UUID(as_uuid=True), ForeignKey("assessment_automations.id", ondelete="CASCADE"), nullable=True
    )
    template_id = Column(
        UUID(as_uuid=True), ForeignKey("assessment_templates.id", ondelete="SET NULL"), nullable=True
    )
    candidate_id = Column(UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False)
    application_id = Column(
        UUID(as_uuid=True), ForeignKey("candidate_applications.id", ondelete="CASCADE"), nullable=False
    )
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True)

    answers = Column(JSONB, nullable=True)  # Candidate's responses
    score = Column(Integer, nullable=True)  # Overall score
    aptitude_score = Column(Integer, nullable=True)  # Separate score for aptitude
    coding_score = Column(Integer, nullable=True)  # Separate score for coding
    status = Column(String, default="STARTED")  # STARTED, COMPLETED, EXPIRED

    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    # Relationships
    automation = relationship("AssessmentAutomation", back_populates="attempts")
    template = relationship("AssessmentTemplate")
    candidate = relationship("Candidate")
    application = relationship("CandidateApplication")
