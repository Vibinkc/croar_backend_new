from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.enterprise.assessment import AssessmentType


class EmailTemplateSimple(BaseModel):
    id: UUID
    name: str

    model_config = ConfigDict(from_attributes=True)


class AssessmentAutomationBase(BaseModel):
    job_requirement_id: UUID
    stage_index: int
    stage_name: str | None = None
    criteria: str
    type: AssessmentType
    topic: str
    question_count: int = 10
    test_duration: int = 30
    is_enabled: bool = True
    is_immediate: bool = True
    auto_move: bool = False
    send_at: datetime | None = None
    template_id: UUID | None = None
    email_template_id: UUID | None = None


class AssessmentAutomationCreate(AssessmentAutomationBase):
    email_template_id: UUID | None = None
    generated_questions: list[dict[str, Any]] | None = None


class AssessmentAutomationUpdate(BaseModel):
    stage_index: int | None = None
    stage_name: str | None = None
    criteria: str | None = None
    type: AssessmentType | None = None
    topic: str | None = None
    question_count: int | None = None
    generated_questions: list[dict[str, Any]] | None = None
    test_duration: int | None = None
    is_enabled: bool | None = None
    is_immediate: bool | None = None
    auto_move: bool | None = None
    send_at: datetime | None = None
    email_template_id: UUID | None = None


class AssessmentAutomationResponse(AssessmentAutomationBase):
    id: UUID
    generated_questions: list[dict[str, Any]] | None = None
    email_template: EmailTemplateSimple | None = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

    @field_validator("send_at", "created_at", mode="after", check_fields=False)
    @classmethod
    def ensure_utc(cls, v: datetime | None) -> datetime | None:
        if v and v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v


class AssessmentTemplateBase(BaseModel):
    name: str
    type: AssessmentType
    topic: str
    question_count: int = 10
    test_duration: int = 30
    email_template_id: UUID | None = None


class AssessmentTemplateCreate(AssessmentTemplateBase):
    email_template_id: UUID | None = None
    generated_questions: list[dict[str, Any]] | None = None


class AssessmentTemplateUpdate(BaseModel):
    name: str | None = None
    type: AssessmentType | None = None
    topic: str | None = None
    question_count: int | None = None
    test_duration: int | None = None
    generated_questions: list[dict[str, Any]] | None = None
    email_template_id: UUID | None = None


class AssessmentTemplateResponse(AssessmentTemplateBase):
    id: UUID
    generated_questions: list[dict[str, Any]] | None = None
    email_template: EmailTemplateSimple | None = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)

    @field_validator(
        "created_at", "updated_at", "started_at", "completed_at", mode="after", check_fields=False
    )
    @classmethod
    def ensure_utc_generic(cls, v: datetime | None) -> datetime | None:
        if v and v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v


class AssessmentAttemptBase(BaseModel):
    automation_id: UUID | None = None
    template_id: UUID | None = None
    candidate_id: UUID
    application_id: UUID
    answers: dict[str, Any] | None = None
    score: int | None = None
    aptitude_score: int | None = None
    coding_score: int | None = None
    status: str = "STARTED"


class AssessmentAttemptCreate(BaseModel):
    automation_id: UUID | None = None
    template_id: UUID | None = None
    email: str  # For verification


class AssessmentAttemptResponse(AssessmentAttemptBase):
    id: UUID
    started_at: datetime
    completed_at: datetime | None = None

    # Metadata for UI
    topic: str | None = None
    type: AssessmentType | None = None

    model_config = ConfigDict(from_attributes=True)


class BulkSendAssessmentRequest(BaseModel):
    application_ids: list[UUID]
    template_id: UUID
