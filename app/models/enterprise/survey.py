import uuid
from typing import Optional, List
from sqlalchemy import String, Integer, TIMESTAMP, func, ForeignKey, Text, Boolean, Date, Enum as SqlEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text
import enum
from . import EnterpriseBase

class SurveyQuestionType(str, enum.Enum):
    RATING = "RATING" # 1-5 or custom scale
    TEXT = "TEXT" # Open-ended
    MCQ = "MCQ" # Multiple Choice

class SurveyInstanceStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"

class SurveyInviteStatus(str, enum.Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"

class SurveyType(EnterpriseBase):
    """The 13 types (Engagement, DEI, eNPS, etc.)"""
    __tablename__ = "survey_types"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()"))
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

class SurveyTemplate(EnterpriseBase):
    __tablename__ = "survey_templates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()"))
    survey_type_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("survey_types.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now())

    survey_type = relationship("SurveyType")
    questions = relationship("SurveyQuestion", back_populates="template", cascade="all, delete-orphan", order_by="SurveyQuestion.order")

class SurveyQuestion(EnterpriseBase):
    __tablename__ = "survey_questions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()"))
    template_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("survey_templates.id", ondelete="CASCADE"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[SurveyQuestionType] = mapped_column(SqlEnum(SurveyQuestionType), default=SurveyQuestionType.RATING)
    order: Mapped[int] = mapped_column(Integer, default=0)
    
    # Configuration for Rating/Scale
    scale_min: Mapped[int] = mapped_column(Integer, default=1)
    scale_max: Mapped[int] = mapped_column(Integer, default=5)
    
    # Options for MCQ (stored as JSON string or a simple list)
    options: Mapped[Optional[str]] = mapped_column(Text, nullable=True) # JSON array of strings

    template = relationship("SurveyTemplate", back_populates="questions")

class SurveyInstance(EnterpriseBase):
    """An execution of a survey template"""
    __tablename__ = "survey_instances"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()"))
    template_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("survey_templates.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False) # e.g. "Annual Engagement 2025"
    
    start_date: Mapped[Date] = mapped_column(Date, nullable=False)
    end_date: Mapped[Date] = mapped_column(Date, nullable=False)
    status: Mapped[SurveyInstanceStatus] = mapped_column(SqlEnum(SurveyInstanceStatus), default=SurveyInstanceStatus.DRAFT)
    
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    target_group: Mapped[str] = mapped_column(String(50), default="ALL") # ALL, DEPT, CUSTOM
    
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    
    template = relationship("SurveyTemplate")
    invites = relationship("SurveyInvite", back_populates="instance", cascade="all, delete-orphan")

class SurveyInvite(EnterpriseBase):
    """A specific link between an employee and a survey instance"""
    __tablename__ = "survey_invites"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()"))
    instance_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("survey_instances.id", ondelete="CASCADE"), nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    
    # unique token for filling the survey
    token: Mapped[str] = mapped_column(String(100), unique=True, default=lambda: str(uuid.uuid4()))
    status: Mapped[SurveyInviteStatus] = mapped_column(SqlEnum(SurveyInviteStatus), default=SurveyInviteStatus.PENDING)
    
    completed_at: Mapped[Optional[TIMESTAMP]] = mapped_column(TIMESTAMP, nullable=True)

    instance = relationship("SurveyInstance", back_populates="invites")
    employee = relationship("Employee")
    responses = relationship("SurveyResponse", back_populates="invite", cascade="all, delete-orphan")

class SurveyResponse(EnterpriseBase):
    __tablename__ = "survey_responses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()"))
    invite_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("survey_invites.id", ondelete="CASCADE"), nullable=False)
    question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("survey_questions.id", ondelete="CASCADE"), nullable=False)
    
    answer_value: Mapped[Optional[int]] = mapped_column(Integer, nullable=True) # for RATING/MCQ index
    answer_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True) # for TEXT

    invite = relationship("SurveyInvite", back_populates="responses")
    question = relationship("SurveyQuestion")
