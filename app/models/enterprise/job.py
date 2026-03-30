import uuid
from typing import Optional, List
from sqlalchemy import String, Integer, TIMESTAMP, func, ForeignKey, Text, Boolean, Numeric, SmallInteger
from sqlalchemy.dialects.postgresql import UUID, ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text
from . import EnterpriseBase
from .company import Company

class JobStatus(EnterpriseBase):
    __tablename__ = "job_statuses"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=True)

class JobRequirement(EnterpriseBase):
    __tablename__ = "job_requirements"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()"))
    
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    required_skills: Mapped[List[str]] = mapped_column(ARRAY(Text), nullable=True)
    
    experience_min: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    experience_max: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    job_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    work_mode: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    department: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    auto_fit_analysis: Mapped[bool] = mapped_column(Boolean, default=False)
    
    salary_min: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    salary_max: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    salary_currency: Mapped[Optional[str]] = mapped_column(String(10), default="INR")
    salary_frequency: Mapped[Optional[str]] = mapped_column(String(50), default="Yearly")
    
    notice_period_max: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    application_fields: Mapped[Optional[List[dict]]] = mapped_column(JSONB, nullable=True)
    workflow_stages: Mapped[Optional[List[dict]]] = mapped_column(JSONB, nullable=True)
    
    status_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("job_statuses.id"), nullable=False)
    company_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True)
    
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[Optional[TIMESTAMP]] = mapped_column(TIMESTAMP, nullable=True)

    status = relationship("JobStatus")
    company = relationship("Company")
    assessment_automations = relationship("AssessmentAutomation", back_populates="job", cascade="all, delete-orphan")
    mail_automations = relationship("MailAutomation", back_populates="job", cascade="all, delete-orphan")
    onboarding_automations = relationship("OnboardingAutomation", back_populates="job", cascade="all, delete-orphan")


class JobPosting(EnterpriseBase):
    __tablename__ = "job_postings"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()"))
    
    job_requirement_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("job_requirements.id", ondelete="CASCADE"), nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    posted_at: Mapped[Optional[TIMESTAMP]] = mapped_column(TIMESTAMP, nullable=True)
    
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now())

    job_requirement = relationship("JobRequirement", backref="postings")

