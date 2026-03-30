from pydantic import BaseModel, ConfigDict, field_validator
from uuid import UUID
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from app.models.enterprise.assessment import AssessmentType

class EmailTemplateSimple(BaseModel):
    id: UUID
    name: str
    
    model_config = ConfigDict(from_attributes=True)

class AssessmentAutomationBase(BaseModel):
    job_requirement_id: UUID
    stage_index: int
    stage_name: Optional[str] = None
    criteria: str
    type: AssessmentType
    topic: str
    question_count: int = 10
    test_duration: int = 30
    is_enabled: bool = True
    is_immediate: bool = True
    auto_move: bool = False
    send_at: Optional[datetime] = None
    template_id: Optional[UUID] = None
    email_template_id: Optional[UUID] = None

class AssessmentAutomationCreate(AssessmentAutomationBase):
    email_template_id: Optional[UUID] = None
    generated_questions: Optional[List[Dict[str, Any]]] = None

class AssessmentAutomationUpdate(BaseModel):
    stage_index: Optional[int] = None
    stage_name: Optional[str] = None
    criteria: Optional[str] = None
    type: Optional[AssessmentType] = None
    topic: Optional[str] = None
    question_count: Optional[int] = None
    generated_questions: Optional[List[Dict[str, Any]]] = None
    test_duration: Optional[int] = None
    is_enabled: Optional[bool] = None
    is_immediate: Optional[bool] = None
    auto_move: Optional[bool] = None
    send_at: Optional[datetime] = None
    email_template_id: Optional[UUID] = None

class AssessmentAutomationResponse(AssessmentAutomationBase):
    id: UUID
    generated_questions: Optional[List[Dict[str, Any]]] = None
    email_template: Optional[EmailTemplateSimple] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

    @field_validator("send_at", "created_at", mode="after", check_fields=False)
    @classmethod
    def ensure_utc(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v

class AssessmentTemplateBase(BaseModel):
    name: str
    type: AssessmentType
    topic: str
    question_count: int = 10
    test_duration: int = 30
    email_template_id: Optional[UUID] = None

class AssessmentTemplateCreate(AssessmentTemplateBase):
    email_template_id: Optional[UUID] = None
    generated_questions: Optional[List[Dict[str, Any]]] = None

class AssessmentTemplateUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[AssessmentType] = None
    topic: Optional[str] = None
    question_count: Optional[int] = None
    test_duration: Optional[int] = None
    generated_questions: Optional[List[Dict[str, Any]]] = None
    email_template_id: Optional[UUID] = None

class AssessmentTemplateResponse(AssessmentTemplateBase):
    id: UUID
    generated_questions: Optional[List[Dict[str, Any]]] = None
    email_template: Optional[EmailTemplateSimple] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)

    @field_validator("created_at", "updated_at", "started_at", "completed_at", mode="after", check_fields=False)
    @classmethod
    def ensure_utc_generic(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v

class AssessmentAttemptBase(BaseModel):
    automation_id: Optional[UUID] = None
    template_id: Optional[UUID] = None
    candidate_id: UUID
    application_id: UUID
    answers: Optional[Dict[str, Any]] = None
    score: Optional[int] = None
    aptitude_score: Optional[int] = None
    coding_score: Optional[int] = None
    status: str = "STARTED"

class AssessmentAttemptCreate(BaseModel):
    automation_id: Optional[UUID] = None
    template_id: Optional[UUID] = None
    email: str  # For verification

class AssessmentAttemptResponse(AssessmentAttemptBase):
    id: UUID
    started_at: datetime
    completed_at: Optional[datetime] = None
    
    # Metadata for UI
    topic: Optional[str] = None
    type: Optional[AssessmentType] = None
    
    model_config = ConfigDict(from_attributes=True)

class BulkSendAssessmentRequest(BaseModel):
    application_ids: List[UUID]
    template_id: UUID
