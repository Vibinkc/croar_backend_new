from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.schemas.enterprise.assessment import EmailTemplateSimple


class InterviewBase(BaseModel):
    title: str
    description: str | None = None
    topic: str | None = None
    duration: int = 30
    difficulty: str = "Intermediate"
    require_video: bool = True
    type: str = "VIDEO"
    plan: dict[str, Any] | None = None
    avatar_config: dict[str, Any] | None = None
    settings: dict[str, Any] | None = None


class InterviewCreate(InterviewBase):
    pass


class InterviewResponse(InterviewBase):
    id: UUID
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class InterviewAutomationBase(BaseModel):
    job_requirement_id: UUID
    stage_index: int
    stage_name: str | None = None
    criteria: str

    start_time: str = "09:00"
    end_time: str = "17:00"
    daily_limit: int = 5

    email_template_id: UUID | None = None
    is_enabled: bool = True
    auto_move: bool = False

    start_date: date | None = None
    end_date: date | None = None

    interviewer_email: str | None = None
    google_meet_link: str | None = None
    time_slots: list[str] | None = None

    interview_type: str = "GMEET"
    interview_template_id: UUID | None = None


class InterviewAutomationCreate(InterviewAutomationBase):
    pass


class InterviewAutomationUpdate(BaseModel):
    job_requirement_id: UUID | None = None
    stage_index: int | None = None
    stage_name: str | None = None
    criteria: str | None = None

    start_time: str | None = None
    end_time: str | None = None
    daily_limit: int | None = None

    email_template_id: UUID | None = None
    is_enabled: bool | None = None
    auto_move: bool | None = None

    start_date: date | None = None
    end_date: date | None = None

    interviewer_email: str | None = None
    google_meet_link: str | None = None
    time_slots: list[str] | None = None
    interview_type: str | None = None
    interview_template_id: UUID | None = None


class InterviewAutomationResponse(InterviewAutomationBase):
    id: UUID
    created_at: datetime
    email_template: EmailTemplateSimple | None = None

    class Config:
        from_attributes = True


class InterviewScheduleBase(BaseModel):
    interview_id: UUID | None = None
    automation_id: UUID | None = None
    application_id: UUID | None = None
    interviewer_id: UUID | None = None
    scheduled_time: datetime
    meeting_link: str | None = None
    status: str = "SCHEDULED"


class InterviewScheduleCreate(InterviewScheduleBase):
    pass


class InterviewScheduleResponse(InterviewScheduleBase):
    id: UUID
    created_at: datetime

    class Config:
        from_attributes = True


class InterviewAttemptBase(BaseModel):
    schedule_id: UUID | None = None
    candidate_id: UUID
    overall_score: float | None = None


class InterviewAttemptCreate(InterviewAttemptBase):
    transcript: dict[str, Any] | None = None
    ai_feedback: dict[str, Any] | None = None


class InterviewAttemptResponse(InterviewAttemptBase):
    id: UUID
    transcript: dict[str, Any] | None = None
    ai_feedback: dict[str, Any] | None = None
    created_at: datetime

    class Config:
        from_attributes = True
