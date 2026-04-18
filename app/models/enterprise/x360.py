import enum
import uuid

from sqlalchemy import TIMESTAMP, Boolean, Date, ForeignKey, Integer, String, Text, func
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text

from .base import EnterpriseBase


class QuestionType(enum.StrEnum):
    RATING = "RATING"
    TEXT = "TEXT"


class QuestionCategory(enum.StrEnum):
    PERFORMANCE = "PERFORMANCE"
    ENGAGEMENT = "ENGAGEMENT"
    CORE_VALUES = "CORE_VALUES"
    LEADERSHIP = "LEADERSHIP"
    TECHNICAL_SKILLS = "TECHNICAL_SKILLS"
    SOFT_SKILLS = "SOFT_SKILLS"
    COMMUNICATION = "COMMUNICATION"
    TEAMWORK = "TEAMWORK"
    ADAPTABILITY = "ADAPTABILITY"
    CULTURE = "CULTURE"
    STRATEGY = "STRATEGY"
    INNOVATION = "INNOVATION"
    PROBLEM_SOLVING = "PROBLEM_SOLVING"


class RelationType(enum.StrEnum):
    SELF = "SELF"
    MANAGER = "MANAGER"
    PEER = "PEER"
    REPORT = "REPORT"


class CycleStatus(enum.StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class AssignmentStatus(enum.StrEnum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"


class X360Question(EnterpriseBase):
    __tablename__ = "x360_questions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[QuestionType] = mapped_column(SqlEnum(QuestionType), default=QuestionType.RATING)
    category: Mapped[str] = mapped_column(Text, default="PERFORMANCE")
    active_flag: Mapped[bool] = mapped_column(Boolean, default=True)

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )

    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )


class X360AssessmentTemplate(EnterpriseBase):
    __tablename__ = "x360_assessment_templates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )

    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )

    questions = relationship(
        "X360TemplateQuestion", back_populates="template", order_by="X360TemplateQuestion.order"
    )


class X360TemplateQuestion(EnterpriseBase):
    __tablename__ = "x360_template_questions"

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("x360_assessment_templates.id", ondelete="CASCADE"), primary_key=True
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("x360_questions.id", ondelete="CASCADE"), primary_key=True
    )
    order: Mapped[int] = mapped_column(Integer, default=0)

    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=True
    )  # Added

    template = relationship("X360AssessmentTemplate", back_populates="questions")
    question = relationship("X360Question")


class X360AssessmentCycle(EnterpriseBase):
    __tablename__ = "x360_assessment_cycles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    start_date: Mapped[Date] = mapped_column(Date, nullable=False)
    end_date: Mapped[Date] = mapped_column(Date, nullable=False)
    status: Mapped[CycleStatus] = mapped_column(SqlEnum(CycleStatus), default=CycleStatus.DRAFT)

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("x360_assessment_templates.id", ondelete="SET NULL"), nullable=True
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )

    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )

    template = relationship("X360AssessmentTemplate")
    assignments = relationship("X360AssessmentAssignment", back_populates="cycle")


class X360AssessmentAssignment(EnterpriseBase):
    __tablename__ = "x360_assessment_assignments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    cycle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("x360_assessment_cycles.id", ondelete="CASCADE"), nullable=False
    )
    ratee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    rater_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    relation: Mapped[RelationType] = mapped_column(SqlEnum(RelationType), nullable=False)
    status: Mapped[AssignmentStatus] = mapped_column(
        SqlEnum(AssignmentStatus), default=AssignmentStatus.PENDING
    )

    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=True
    )  # Added

    completed_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())

    cycle = relationship("X360AssessmentCycle", back_populates="assignments")
    ratee = relationship("Employee", foreign_keys=[ratee_id])
    rater = relationship("Employee", foreign_keys=[rater_id])
    responses = relationship("X360AssessmentResponse", back_populates="assignment")


class X360AssessmentResponse(EnterpriseBase):
    __tablename__ = "x360_assessment_responses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("x360_assessment_assignments.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("x360_questions.id", ondelete="CASCADE"), nullable=False
    )

    answer_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=True
    )  # Added

    assignment = relationship("X360AssessmentAssignment", back_populates="responses")
    question = relationship("X360Question")


class X360EmployeeRaterMap(EnterpriseBase):
    __tablename__ = "x360_employee_rater_maps"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    rater_employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    relation: Mapped[RelationType] = mapped_column(SqlEnum(RelationType), nullable=False)

    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=True
    )  # Added

    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())

    employee = relationship("Employee", foreign_keys=[employee_id])
    rater = relationship("Employee", foreign_keys=[rater_employee_id])
