from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, field_validator


class CandidateBase(BaseModel):
    id: UUID
    full_name: str | None
    email: str | None
    phone: str | None = None
    skills: list[str] = []
    parsed_data: dict[str, Any] | None = None

    @field_validator("skills", mode="before")
    @classmethod
    def ensure_list(cls, v):
        if v is None:
            return []
        return v

    class Config:
        from_attributes = True


class ApplicationResponse(BaseModel):
    id: UUID
    candidate_id: UUID
    job_requirement_id: UUID
    status_id: int
    current_stage: int
    ai_match_score: float | None
    ai_feedback: dict[str, Any] | None = None
    assessment_score: int | None = None
    aptitude_score: int | None = None
    coding_score: int | None = None
    ai_interview_score: float | None = None
    candidate: CandidateBase
    applied_at: datetime | None
    onboarding_id: UUID | None = None

    class Config:
        from_attributes = True


class UpdateStageRequest(BaseModel):
    new_stage: int
