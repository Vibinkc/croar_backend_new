from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.enterprise.applications import ApplicationResponse
from app.schemas.enterprise.communication import EmailTemplateResponse


class OnboardingTemplateBase(BaseModel):
    name: str
    description: str | None = None
    sections: list[str] = []  # e.g. ["job_info", "personal_info", "education_info", "documents"]
    required_documents: list[dict[str, Any]] = []  # e.g. [{"name": "Aadhar Card", "description": "ID"}]
    form_config: dict[str, Any] = {}


class OnboardingTemplateCreate(OnboardingTemplateBase):
    pass


class OnboardingTemplateResponse(OnboardingTemplateBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OnboardingStatusResponse(BaseModel):
    id: int
    name: str
    description: str | None = None

    model_config = ConfigDict(from_attributes=True)


class OnboardingDocumentResponse(BaseModel):
    id: UUID
    onboarding_id: UUID
    name: str
    doc_type: str | None = None
    file_path: str | None = None
    status: str
    due_date: datetime | None = None
    comment: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OnboardingActivityResponse(BaseModel):
    id: UUID
    onboarding_id: UUID
    action: str
    performed_by: str
    timestamp: datetime
    metadata_info: dict[str, Any] | None = None

    model_config = ConfigDict(from_attributes=True)


class OnboardingTaskResponse(BaseModel):
    id: UUID
    onboarding_id: UUID
    title: str
    description: str | None = None
    priority: str
    status: str
    due_date: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class OnboardingNoteResponse(BaseModel):
    id: UUID
    onboarding_id: UUID
    content: str
    author_name: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OnboardingResponse(BaseModel):
    id: UUID
    application_id: UUID
    onboarding_code: str
    status_id: int
    status: OnboardingStatusResponse | None = None
    template_id: UUID | None = None
    template: OnboardingTemplateResponse | None = None

    job_info: dict[str, Any] | None = None
    personal_info: dict[str, Any] | None = None
    education_info: dict[str, Any] | None = None
    other_info: dict[str, Any] | None = None
    form_data: dict[str, Any] | None = None

    candidate_email: str | None = None
    job_title: str | None = None
    company_name: str | None = None
    company_logo: str | None = None

    initiation_date: datetime
    completed_at: datetime | None = None

    application: ApplicationResponse | None = None

    documents: list[OnboardingDocumentResponse] = []
    activities: list[OnboardingActivityResponse] = []
    tasks: list[OnboardingTaskResponse] = []
    notes: list[OnboardingNoteResponse] = []
    rejected_fields: list[str] = []

    model_config = ConfigDict(from_attributes=True)


class OnboardingInitiateRequest(BaseModel):
    application_id: UUID
    template_id: UUID | None = None


class OnboardingUpdateRequest(BaseModel):
    status_id: int | None = None
    job_info: dict[str, Any] | None = None
    personal_info: dict[str, Any] | None = None
    education_info: dict[str, Any] | None = None
    other_info: dict[str, Any] | None = None
    form_data: dict[str, Any] | None = None


class OnboardingResubmitRequest(BaseModel):
    reason: str
    rejected_document_ids: list[UUID] | None = []
    rejected_fields: list[str] | None = []


class OnboardingApproveRequest(BaseModel):
    notes: str | None = None


class OnboardingNoteCreate(BaseModel):
    content: str


class OnboardingTaskCreate(BaseModel):
    title: str
    description: str | None = None
    priority: str = "Medium"
    due_date: datetime | None = None


class OnboardingDocumentRequest(BaseModel):
    name: str
    due_date: datetime | None = None


class OnboardingAutomationBase(BaseModel):
    job_requirement_id: UUID
    stage_index: int
    stage_name: str | None = None
    template_id: UUID | None = None
    email_template_id: UUID | None = None
    is_enabled: bool = True
    auto_move: bool = False


class OnboardingAutomationCreate(OnboardingAutomationBase):
    pass


class OnboardingAutomationUpdate(BaseModel):
    stage_index: int | None = None
    stage_name: str | None = None
    template_id: UUID | None = None
    email_template_id: UUID | None = None
    is_enabled: bool | None = None
    auto_move: bool | None = None


class OnboardingAutomationResponse(OnboardingAutomationBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    template: OnboardingTemplateResponse | None = None
    email_template: EmailTemplateResponse | None = None

    model_config = ConfigDict(from_attributes=True)
