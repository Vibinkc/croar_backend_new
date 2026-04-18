from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class EmailTemplateBase(BaseModel):
    name: str = Field(..., description="Name of the template")
    subject: str = Field(..., description="Email subject")
    body: str = Field(..., description="Email body content", min_length=10)
    category: str | None = Field(
        "GENERAL", description="Category of the template (GENERAL, ASSESSMENT, INTERVIEW, ONBOARDING)"
    )
    variables: list[str] | None = Field(default=[], description="List of variable placeholders")


class EmailTemplateCreate(EmailTemplateBase):
    pass


class EmailTemplateUpdate(BaseModel):
    name: str | None = None
    subject: str | None = None
    body: str | None = None
    category: str | None = None
    variables: list[str] | None = None


class EmailTemplateResponse(EmailTemplateBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class EmailLogResponse(BaseModel):
    id: UUID
    direction: str
    sender_email: str | None
    recipient_email: str
    subject: str
    body: str
    status: str
    is_read: bool
    error_message: str | None
    sent_at: datetime
    message_id: str | None
    template_id: UUID | None
    candidate_id: UUID | None
    application_id: UUID | None
    automation_id: UUID | None
    company_id: UUID | None

    class Config:
        from_attributes = True


class EmailSendRequest(BaseModel):
    recipient_ids: list[UUID] | None = Field(None, description="List of Candidate IDs")
    recipient_emails: list[str] | None = Field(None, description="List of direct email addresses")
    template_id: UUID | None = Field(None, description="Template ID to use")
    job_id: UUID | None = Field(None)

    # Overrides
    subject: str | None = Field(None)
    body: str | None = Field(None)
    custom_variables: dict[str, Any] | None = Field({}, description="Key-value pairs for custom variables")


class EmailDraftRequest(BaseModel):
    purpose: str = Field(..., description="Purpose of the email (e.g. Schedule Interview)")
    candidate_name: str | None = None
    job_title: str | None = None
    tone: str | None = Field("professional", description="Tone of the email")
    additional_context: str | None = None


class TemplateGenerationRequest(BaseModel):
    purpose: str = Field(..., description="Goal of the template (e.g. Reject candidate after interview)")
    tone: str = Field("professional", description="Tone of the email")


# --- Mail Automation Schemas ---


class MailAutomationCreate(BaseModel):
    job_requirement_id: UUID
    stage_index: int = Field(..., ge=1, description="Hiring round number (1-based)")
    stage_name: str | None = Field(None, description="Human-readable round label")
    criteria: str = Field(
        ..., min_length=1, description="Free-text condition, e.g. 'AI score > 80' or 'Interview passed'"
    )
    template_id: UUID
    auto_move: bool = False
    is_enabled: bool = True
    is_immediate: bool = True
    send_at: datetime | None = None


class MailAutomationUpdate(BaseModel):
    stage_index: int | None = Field(None, ge=1)
    stage_name: str | None = None
    criteria: str | None = None
    template_id: UUID | None = None
    auto_move: bool | None = None
    is_enabled: bool | None = None
    is_immediate: bool | None = None
    send_at: datetime | None = None


class MailAutomationResponse(BaseModel):
    id: UUID
    job_requirement_id: UUID
    stage_index: int
    stage_name: str | None
    criteria: str
    template_id: UUID
    auto_move: bool
    is_enabled: bool
    is_immediate: bool
    send_at: datetime | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

    @field_validator("send_at", "created_at", "updated_at", mode="after")
    @classmethod
    def ensure_utc(cls, v: datetime | None) -> datetime | None:
        if v and v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v
