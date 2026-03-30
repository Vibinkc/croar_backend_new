from typing import List, Optional, Any
from pydantic import BaseModel, Field, field_validator
from datetime import datetime, timezone
from uuid import UUID

class EmailTemplateBase(BaseModel):
    name: str = Field(..., description="Name of the template")
    subject: str = Field(..., description="Email subject")
    body: str = Field(..., description="Email body content", min_length=10)
    variables: Optional[List[str]] = Field(default=[], description="List of variable placeholders")

class EmailTemplateCreate(EmailTemplateBase):
    pass

class EmailTemplateUpdate(BaseModel):
    name: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    variables: Optional[List[str]] = None

class EmailTemplateResponse(EmailTemplateBase):
    id: UUID
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

class EmailSendRequest(BaseModel):
    recipient_ids: Optional[List[UUID]] = Field(None, description="List of Candidate IDs")
    recipient_emails: Optional[List[str]] = Field(None, description="List of direct email addresses")
    template_id: Optional[UUID] = Field(None, description="Template ID to use")
    job_id: Optional[UUID] = Field(None)
    
    # Overrides
    subject: Optional[str] = Field(None)
    body: Optional[str] = Field(None)
    custom_variables: Optional[dict] = Field({}, description="Key-value pairs for custom variables")

class EmailDraftRequest(BaseModel):
    purpose: str = Field(..., description="Purpose of the email (e.g. Schedule Interview)")
    candidate_name: Optional[str] = None
    job_title: Optional[str] = None
    tone: Optional[str] = Field("professional", description="Tone of the email")
    additional_context: Optional[str] = None

class TemplateGenerationRequest(BaseModel):
    purpose: str = Field(..., description="Goal of the template (e.g. Reject candidate after interview)")
    tone: str = Field("professional", description="Tone of the email")


# --- Mail Automation Schemas ---

class MailAutomationCreate(BaseModel):
    job_requirement_id: UUID
    stage_index: int = Field(..., ge=1, description="Hiring round number (1-based)")
    stage_name: Optional[str] = Field(None, description="Human-readable round label")
    criteria: str = Field(..., min_length=1, description="Free-text condition, e.g. 'AI score > 80' or 'Interview passed'")
    template_id: UUID
    auto_move: bool = False
    is_enabled: bool = True
    is_immediate: bool = True
    send_at: Optional[datetime] = None


class MailAutomationUpdate(BaseModel):
    stage_index: Optional[int] = Field(None, ge=1)
    stage_name: Optional[str] = None
    criteria: Optional[str] = None
    template_id: Optional[UUID] = None
    auto_move: Optional[bool] = None
    is_enabled: Optional[bool] = None
    is_immediate: Optional[bool] = None
    send_at: Optional[datetime] = None


class MailAutomationResponse(BaseModel):
    id: UUID
    job_requirement_id: UUID
    stage_index: int
    stage_name: Optional[str]
    criteria: str
    template_id: UUID
    auto_move: bool
    is_enabled: bool
    is_immediate: bool
    send_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

    @field_validator("send_at", "created_at", "updated_at", mode="after")
    @classmethod
    def ensure_utc(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v
