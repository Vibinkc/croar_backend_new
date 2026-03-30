from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from datetime import datetime
from uuid import UUID

class JobPostingBase(BaseModel):
    platform: str
    external_id: Optional[str] = None
    status: Optional[str] = "Pending"

class JobMetrics(BaseModel):
    pipeline: int = 0
    submitted: int = 0
    interviews: int = 0
    rejected: int = 0
    onboarded: int = 0

class JobStageResponse(BaseModel):
    id: int
    name: str
    count: int = 0

class JobRequirementCreate(BaseModel):
    title: str
    description: str
    required_skills: Optional[List[str]] = []
    experience_min: Optional[int] = None
    experience_max: Optional[int] = None
    location: Optional[str] = None
    job_type: Optional[str] = None
    work_mode: Optional[str] = None
    department: Optional[str] = None
    auto_fit_analysis: bool = False
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_currency: Optional[str] = "INR"
    salary_frequency: Optional[str] = "Yearly"
    notice_period_max: Optional[int] = None
    application_fields: Optional[List[dict]] = []
    workflow_stages: Optional[List[dict]] = []
    status_id: int = 1 
    company_id: Optional[UUID] = None
    target_platforms: Optional[List[str]] = [] 

    @field_validator('required_skills', 'target_platforms', mode='before')
    @classmethod
    def ensure_list(cls, v):
        if v is None:
            return []
        return v

class JobRequirementResponse(JobRequirementCreate):
    id: UUID
    company_id: Optional[UUID] = None

    created_at: datetime
    postings: List[JobPostingBase] = []
    metrics: Optional[JobMetrics] = None
    stages: List[JobStageResponse] = []

    class Config:
        from_attributes = True

class PublishJobRequest(BaseModel):
    platforms: List[str]

class JobRequirementUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    required_skills: Optional[List[str]] = None
    experience_min: Optional[int] = None
    experience_max: Optional[int] = None
    location: Optional[str] = None
    job_type: Optional[str] = None
    work_mode: Optional[str] = None
    department: Optional[str] = None
    auto_fit_analysis: Optional[bool] = None
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_currency: Optional[str] = None
    salary_frequency: Optional[str] = None
    notice_period_max: Optional[int] = None
    application_fields: Optional[List[dict]] = None
    workflow_stages: Optional[List[dict]] = None
    status_id: Optional[int] = None
    company_id: Optional[UUID] = None

class JDGenerationRequest(BaseModel):
    title: str
    existing_description: Optional[str] = ""
    location: Optional[str] = ""
    experience_min: Optional[str] = "0"
    experience_max: Optional[str] = "5"
    generate_workflow: bool = False

class WorkflowGenerationRequest(BaseModel):
    title: str
    description: str
