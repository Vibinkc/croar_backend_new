import uuid
from typing import Optional, List
from sqlalchemy import String, Integer, TIMESTAMP, func, ForeignKey, Text, Boolean, Numeric, SmallInteger, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text
from . import EnterpriseBase
from .job import JobRequirement

class ApplicationStatus(EnterpriseBase):
    __tablename__ = "application_statuses"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=True)

class Candidate(EnterpriseBase):
    __tablename__ = "candidates"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()"))
    
    user_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=True), nullable=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    
    total_experience: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    relevant_experience: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    notice_period: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    current_salary: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    expected_salary: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    
    reason_for_change: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    skills: Mapped[List[str]] = mapped_column(ARRAY(Text), nullable=True)
    resume_file_path: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source_platform: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    parsed_data: Mapped[dict] = mapped_column(JSONB, nullable=True)
    
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[Optional[TIMESTAMP]] = mapped_column(TIMESTAMP, nullable=True)

class CandidateApplication(EnterpriseBase):
    __tablename__ = "candidate_applications"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()"))
    
    candidate_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False)
    job_requirement_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("job_requirements.id", ondelete="CASCADE"), nullable=False)
    status_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("application_statuses.id"), nullable=False)
    
    ai_match_score: Mapped[Optional[float]] = mapped_column(Numeric(5, 2), nullable=True)
    skill_match_percent: Mapped[Optional[float]] = mapped_column(Numeric(5, 2), nullable=True)
    experience_fit: Mapped[Optional[float]] = mapped_column(Numeric(5, 2), nullable=True)
    ranking_position: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    ai_feedback: Mapped[dict] = mapped_column(JSONB, nullable=True)
    current_stage: Mapped[int] = mapped_column(Integer, default=1)
    applied_at: Mapped[Optional[TIMESTAMP]] = mapped_column(TIMESTAMP, nullable=True)
    
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[Optional[TIMESTAMP]] = mapped_column(TIMESTAMP, nullable=True)

    candidate = relationship("Candidate", backref="applications")
    job_requirement = relationship("JobRequirement", backref="applications")
    status = relationship("ApplicationStatus")
    assessment_attempts = relationship("AssessmentAttempt", back_populates="application")
    onboarding = relationship("Onboarding", back_populates="application", uselist=False)
    interview_schedules = relationship("InterviewSchedule", back_populates="application")

