from pydantic import BaseModel, field_validator
from typing import Optional, Dict, Any, List
from datetime import datetime
from uuid import UUID

class CandidateBase(BaseModel):
    id: UUID
    full_name: Optional[str]
    email: Optional[str]
    phone: Optional[str] = None
    skills: List[str] = []
    parsed_data: Optional[Dict[str, Any]] = None

    @field_validator('skills', mode='before')
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
    ai_match_score: Optional[float]
    ai_feedback: Optional[Dict[str, Any]] = None
    assessment_score: Optional[int] = None
    aptitude_score: Optional[int] = None
    coding_score: Optional[int] = None
    ai_interview_score: Optional[float] = None
    candidate: CandidateBase
    applied_at: Optional[datetime]
    onboarding_id: Optional[UUID] = None
    
    class Config:
        from_attributes = True

class UpdateStageRequest(BaseModel):
    new_stage: int
