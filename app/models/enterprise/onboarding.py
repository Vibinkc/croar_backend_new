import uuid

from sqlalchemy import TIMESTAMP, Boolean, ForeignKey, Integer, SmallInteger, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text

from . import EnterpriseBase


class OnboardingTemplate(EnterpriseBase):
    __tablename__ = "onboarding_templates"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Configuration for sections (Welcome, Job Info, Personal, Education, Documents)
    sections: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    # Configuration for required documents
    required_documents: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)

    # Detailed form configuration for fields within each section
    form_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    company_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )


class OnboardingStatus(EnterpriseBase):
    __tablename__ = "onboarding_statuses"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=True)


class Onboarding(EnterpriseBase):
    __tablename__ = "onboardings"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )

    application_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidate_applications.id", ondelete="CASCADE"), nullable=False
    )
    onboarding_code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)

    status_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("onboarding_statuses.id"), nullable=False)
    template_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("onboarding_templates.id", ondelete="SET NULL"), nullable=True
    )

    # Information Capture
    job_info: Mapped[dict] = mapped_column(JSONB, nullable=True)
    personal_info: Mapped[dict] = mapped_column(JSONB, nullable=True)
    education_info: Mapped[dict] = mapped_column(JSONB, nullable=True)
    other_info: Mapped[dict] = mapped_column(JSONB, nullable=True)
    form_data: Mapped[dict] = mapped_column(JSONB, nullable=True)
    rejected_fields: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    company_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )

    initiation_date: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now()
    )
    completed_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)

    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)

    application = relationship("CandidateApplication", back_populates="onboarding")
    status = relationship("OnboardingStatus")
    template = relationship("OnboardingTemplate")
    documents = relationship("OnboardingDocument", back_populates="onboarding", cascade="all, delete-orphan")
    activities = relationship("OnboardingActivity", back_populates="onboarding", cascade="all, delete-orphan")
    tasks = relationship("OnboardingTask", back_populates="onboarding", cascade="all, delete-orphan")
    notes = relationship("OnboardingNote", back_populates="onboarding", cascade="all, delete-orphan")

    @property
    def candidate_email(self) -> str | None:
        if self.application and self.application.candidate:
            return self.application.candidate.email
        return None

    @property
    def job_title(self) -> str | None:
        if self.application and self.application.job_requirement:
            return self.application.job_requirement.title
        return None


class OnboardingDocument(EnterpriseBase):
    __tablename__ = "onboarding_documents"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    onboarding_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("onboardings.id", ondelete="CASCADE"), nullable=False
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    doc_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    status: Mapped[str] = mapped_column(String(50), default="Pending")
    due_date: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    company_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )

    onboarding = relationship("Onboarding", back_populates="documents")


class OnboardingActivity(EnterpriseBase):
    __tablename__ = "onboarding_activities"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    onboarding_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("onboardings.id", ondelete="CASCADE"), nullable=False
    )

    action: Mapped[str] = mapped_column(Text, nullable=False)
    performed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    timestamp: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    metadata_info: Mapped[dict] = mapped_column(JSONB, nullable=True)
    company_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )

    onboarding = relationship("Onboarding", back_populates="activities")


class OnboardingTask(EnterpriseBase):
    __tablename__ = "onboarding_tasks"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    onboarding_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("onboardings.id", ondelete="CASCADE"), nullable=False
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[str] = mapped_column(String(20), default="Medium")
    status: Mapped[str] = mapped_column(String(50), default="Pending")

    due_date: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)

    company_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )

    onboarding = relationship("Onboarding", back_populates="tasks")


class OnboardingNote(EnterpriseBase):
    __tablename__ = "onboarding_notes"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    onboarding_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("onboardings.id", ondelete="CASCADE"), nullable=False
    )

    content: Mapped[str] = mapped_column(Text, nullable=False)
    author_name: Mapped[str] = mapped_column(String(255), nullable=False)

    company_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())

    onboarding = relationship("Onboarding", back_populates="notes")


class OnboardingAutomation(EnterpriseBase):
    __tablename__ = "onboarding_automations"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    job_requirement_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_requirements.id", ondelete="CASCADE"), nullable=False
    )

    stage_index: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    stage_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    template_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("onboarding_templates.id", ondelete="SET NULL"), nullable=True
    )
    email_template_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("email_templates.id", ondelete="SET NULL"), nullable=True
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_move: Mapped[bool] = mapped_column(Boolean, default=False)

    company_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )

    job = relationship("JobRequirement")
    template = relationship("OnboardingTemplate")
    email_template = relationship("EmailTemplate")
