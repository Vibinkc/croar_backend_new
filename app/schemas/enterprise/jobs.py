from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from .company import CompanyResponse


class MemberBrief(BaseModel):
    """Compact team-member reference used for job owner/collaborators."""

    id: UUID
    full_name: str
    email: str
    profile_image: str | None = None

    class Config:
        from_attributes = True


class JobActivityOut(BaseModel):
    id: UUID
    action: str
    actor_id: UUID | None = None
    actor_name: str | None = None
    detail: dict[str, Any] | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class AssignJobRequest(BaseModel):
    owner_id: UUID | None = None
    collaborator_ids: list[UUID] = []


class JobPostingBase(BaseModel):
    platform: str
    external_id: str | None = None
    status: str | None = "Pending"

    class Config:
        from_attributes = True


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
    # Length caps match the DB columns (title/location are VARCHAR(255)) so oversized input
    # is rejected as 422 at the schema instead of blowing up as a 500 on insert.
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=1)
    required_skills: list[str] | None = []
    experience_min: int | None = None
    experience_max: int | None = None
    location: str | None = Field(None, max_length=255)
    job_type: str | None = None
    work_mode: str | None = None
    department: str | None = None
    auto_fit_analysis: bool = False
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = "INR"
    salary_frequency: str | None = "Yearly"
    notice_period_max: int | None = None
    application_fields: list[dict[str, Any]] | None = []
    workflow_stages: list[dict[str, Any]] | None = []
    status_id: int = 1
    company_id: UUID | None = None
    target_platforms: list[str] | None = []

    @field_validator("required_skills", "target_platforms", mode="before")
    @classmethod
    def ensure_list(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, list):
            return [str(item) for item in v]
        return [str(v)]


class JobRequirementResponse(JobRequirementCreate):
    id: UUID
    company_id: UUID | None = None
    company: CompanyResponse | None = None

    created_at: datetime
    postings: list[JobPostingBase] = []
    metrics: JobMetrics | None = None
    stages: list[JobStageResponse] = []

    # Ownership / assignment (Team Management)
    owner: MemberBrief | None = None
    collaborators: list[MemberBrief] = Field(default=[], validation_alias="collaborator_users")
    last_viewed_at: datetime | None = None

    class Config:
        from_attributes = True
        populate_by_name = True


class PublishJobRequest(BaseModel):
    platforms: list[str]


class JobRequirementUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    required_skills: list[str] | None = None
    experience_min: int | None = None
    experience_max: int | None = None
    location: str | None = None
    job_type: str | None = None
    work_mode: str | None = None
    department: str | None = None
    auto_fit_analysis: bool | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_frequency: str | None = None
    notice_period_max: int | None = None
    application_fields: list[dict[str, Any]] | None = None
    workflow_stages: list[dict[str, Any]] | None = None
    status_id: int | None = None
    company_id: UUID | None = None


class JDGenerationRequest(BaseModel):
    title: str
    existing_description: str | None = ""
    location: str | None = ""
    # Work arrangement (On-Site / Remote / Hybrid) so the JD doesn't default to "Remote".
    work_mode: str | None = ""
    experience_min: str | None = "0"
    experience_max: str | None = "5"
    # Free-text extra requirements the user wants the AI to fold into the existing description
    # (e.g. "add a section on on-call rotation and mention we use Kubernetes").
    additional_instructions: str | None = ""
    generate_workflow: bool = False


class WorkflowGenerationRequest(BaseModel):
    title: str
    description: str
