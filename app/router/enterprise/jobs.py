from datetime import datetime
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import selectinload

from app.core.ai import generate_job_description_ai
from app.core.dependencies import DBSessionDep, PermissionChecker
from app.core.settings import settings
from app.models.enterprise.assessment import AssessmentAutomation
from app.models.enterprise.candidate import CandidateApplication
from app.models.enterprise.communication import MailAutomation
from app.models.enterprise.company import Company
from app.models.enterprise.interview import InterviewAutomation
from app.models.enterprise.job import JobPosting, JobRequirement
from app.models.enterprise.onboarding import OnboardingAutomation
from app.models.shared.constants import ModuleScope, PermissionAction
from app.schemas.enterprise.jobs import (
    JDGenerationRequest,
    JobMetrics,
    JobRequirementCreate,
    JobRequirementResponse,
    JobRequirementUpdate,
    JobStageResponse,
    PublishJobRequest,
    WorkflowGenerationRequest,
)
from app.services.enterprise.google_jobs import google_jobs_service
from app.services.enterprise.hiring_agent import hiring_agent_service

router = APIRouter(prefix="/jobs", tags=["Enterprise Jobs"])

# Removed get_enterprise_agent helper as it's redundant with PermissionChecker


def normalize_workflow_stages(stages: list[dict[str, object]]) -> list[dict[str, object]]:
    """Ensure stage IDs are sequential strings 1, 2, 3..."""
    if not stages:
        return stages
    for i, stage in enumerate(stages):
        stage["id"] = str(i + 1)
    return stages


@router.post("/", response_model=JobRequirementResponse)
async def create_job(
    request: JobRequirementCreate,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.create))],
) -> JobRequirement:
    """Create a new job requirement."""
    is_consultancy = getattr(getattr(current_user, "company", None), "is_consultancy", False)
    target_company_id = request.company_id or getattr(current_user, "company_id", None)
    workflow_stages = normalize_workflow_stages(request.workflow_stages or [])

    # Validation: If consultancy, they can hire for partners.
    # If not, it must be their own company.
    if target_company_id != getattr(current_user, "company_id", None):
        if not is_consultancy:
            raise HTTPException(status_code=403, detail="Not authorized to hire for other organizations.")
        # Verify it's a partner
        partner_stmt = select(Company).where(
            Company.id == target_company_id, Company.parent_id == getattr(current_user, "company_id", None)
        )
        partner = (await session.execute(partner_stmt)).scalar_one_or_none()
        if not partner:
            raise HTTPException(status_code=403, detail="Target company is not a registered partner node.")

    new_job = JobRequirement(
        **request.model_dump(exclude={"target_platforms", "workflow_stages", "company_id"}),
        workflow_stages=workflow_stages,
        company_id=target_company_id,
    )
    session.add(new_job)
    await session.commit()
    await session.refresh(new_job)

    # Eager load for response
    stmt = (
        select(JobRequirement)
        .options(selectinload(JobRequirement.postings), selectinload(JobRequirement.company))
        .where(JobRequirement.id == new_job.id)
    )
    result = await session.execute(stmt)
    return result.scalar_one()


@router.get("/", response_model=list[JobRequirementResponse])
async def list_jobs(
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.read))],
    company_id: UUID | None = None,
) -> list[JobRequirement]:
    """List all jobs (optionally filtered by partner company)."""
    from sqlalchemy import or_

    is_consultancy = getattr(getattr(current_user, "company", None), "is_consultancy", False)

    stmt = (
        select(JobRequirement)
        .options(selectinload(JobRequirement.postings), selectinload(JobRequirement.company))
        .where(JobRequirement.deleted_at.is_(None))
    )

    if company_id:
        # User explicitly requested a specific company
        if company_id != getattr(current_user, "company_id", None):
            if not is_consultancy:
                raise HTTPException(status_code=403, detail="Access denied.")
            # Verify partner
            partner_stmt = select(Company).where(
                Company.id == company_id, Company.parent_id == getattr(current_user, "company_id", None)
            )
            if not (await session.execute(partner_stmt)).scalar_one_or_none():
                raise HTTPException(status_code=403, detail="Invalid partner context.")
        stmt = stmt.where(JobRequirement.company_id == company_id)
    else:
        # Default view
        if is_consultancy:
            # Show jobs for the consultancy AND all its partners
            partner_ids_stmt = select(Company.id).where(
                Company.parent_id == getattr(current_user, "company_id", None)
            )
            partner_ids = (await session.execute(partner_ids_stmt)).scalars().all()
            stmt = stmt.where(
                or_(
                    JobRequirement.company_id == getattr(current_user, "company_id", None),
                    JobRequirement.company_id.in_(partner_ids),
                )
            )
        else:
            stmt = stmt.where(JobRequirement.company_id == getattr(current_user, "company_id", None))

    result = await session.execute(stmt.order_by(JobRequirement.created_at.desc()))
    jobs = result.scalars().all()
    return list(jobs)


@router.get("/{job_id}", response_model=JobRequirementResponse)
async def get_job(
    job_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.read))],
) -> JobRequirementResponse:
    from sqlalchemy import or_

    is_consultancy = getattr(getattr(current_user, "company", None), "is_consultancy", False)

    stmt = (
        select(JobRequirement)
        .options(selectinload(JobRequirement.postings), selectinload(JobRequirement.company))
        .where(JobRequirement.id == job_id, JobRequirement.deleted_at.is_(None))
    )

    if is_consultancy:
        # Allow if job belongs to consultancy OR any of its partners
        partner_ids_stmt = select(Company.id).where(
            Company.parent_id == getattr(current_user, "company_id", None)
        )
        partner_ids = (await session.execute(partner_ids_stmt)).scalars().all()
        stmt = stmt.where(
            or_(
                JobRequirement.company_id == getattr(current_user, "company_id", None),
                JobRequirement.company_id.in_(partner_ids),
            )
        )
    else:
        stmt = stmt.where(JobRequirement.company_id == getattr(current_user, "company_id", None))

    result = await session.execute(stmt)
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Calculate metrics
    # Stages mapping logic (simplified/dynamic)
    metrics_stmt = (
        select(CandidateApplication.current_stage, func.count(CandidateApplication.id))
        .where(CandidateApplication.job_requirement_id == job_id)
        .group_by(CandidateApplication.current_stage)
    )

    def _to_int(value: Any, default: int = 0) -> int:
        # Stage ids are usually ints but may be missing/non-numeric in stored JSON.
        try:
            return int(cast("Any", value))
        except (TypeError, ValueError):
            return default

    metrics_result = await session.execute(metrics_stmt)
    counts: dict[int, int] = {}
    for stage, count in metrics_result.all():
        counts[_to_int(stage)] = _to_int(count)

    # Dynamic Stages (Rounds)
    stages_to_use = job.workflow_stages or []

    # Explicitly construct the response to ensure stages are included
    response = JobRequirementResponse.model_validate(job)

    response.stages = [
        JobStageResponse(
            id=_to_int(s.get("id", i + 1), i + 1),
            name=str(s.get("name", f"Stage {i + 1}")),
            count=counts.get(_to_int(s.get("id", i + 1), i + 1), 0),
        )
        for i, s in enumerate(stages_to_use)
    ]

    # Metrics calculation
    total_in_flow = sum(counts.values())
    response.metrics = JobMetrics(
        pipeline=total_in_flow,
        submitted=counts.get(1, 0),
        interviews=counts.get(2, 0),
        rejected=counts.get(6, 0),
        onboarded=counts.get(5, 0),
    )

    return response


@router.patch("/{job_id}", response_model=JobRequirementResponse)
async def update_job(
    job_id: UUID,
    request: JobRequirementUpdate,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.update))],
) -> JobRequirement:
    """Update an existing job requisition."""
    stmt = select(JobRequirement).where(
        JobRequirement.id == job_id, JobRequirement.company_id == getattr(current_user, "company_id", None)
    )
    result = await session.execute(stmt)
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    update_data = request.model_dump(exclude_unset=True)
    if update_data.get("workflow_stages"):
        update_data["workflow_stages"] = normalize_workflow_stages(
            cast("list[dict[str, object]]", update_data["workflow_stages"])
        )

    for key, value in update_data.items():
        setattr(job, key, value)

    await session.commit()
    await session.refresh(job)

    # Re-fetch with mappings
    stmt_reload = (
        select(JobRequirement)
        .options(selectinload(JobRequirement.postings), selectinload(JobRequirement.company))
        .where(
            JobRequirement.id == job_id,
            JobRequirement.company_id == getattr(current_user, "company_id", None),
        )
    )
    result_reload = await session.execute(stmt_reload)
    return result_reload.scalar_one()


@router.delete("/{job_id}")
async def delete_job(
    job_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.delete))],
) -> dict[str, str]:
    """Soft delete a job requisition."""
    stmt = select(JobRequirement).where(
        JobRequirement.id == job_id, JobRequirement.company_id == getattr(current_user, "company_id", None)
    )
    result = await session.execute(stmt)
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # 1. Hard-delete ALL automations tied to this job (assessment, mail, interview, onboarding).
    await session.execute(
        delete(AssessmentAutomation).where(AssessmentAutomation.job_requirement_id == job_id)
    )
    await session.execute(delete(MailAutomation).where(MailAutomation.job_requirement_id == job_id))
    await session.execute(delete(InterviewAutomation).where(InterviewAutomation.job_requirement_id == job_id))
    await session.execute(
        delete(OnboardingAutomation).where(OnboardingAutomation.job_requirement_id == job_id)
    )

    # 2. Soft-delete every application for this job EXCEPT hired candidates (status_id == 5),
    #    so hired people keep their records (onboarding, employee profile) intact.
    hired_status_id = 5
    await session.execute(
        update(CandidateApplication)
        .where(
            CandidateApplication.job_requirement_id == job_id,
            CandidateApplication.status_id != hired_status_id,
            CandidateApplication.deleted_at.is_(None),
        )
        .values(deleted_at=datetime.now())
    )

    # 3. Soft-delete the job requirement itself.
    job.deleted_at = cast("Any", datetime.now())
    await session.commit()

    # 3. Notify Google Jobs of deletion
    job_url = f"{settings.frontend_url}/jobs/{job_id}"
    await google_jobs_service.notify_job_update(job_url, update_type="URL_DELETED")

    return {"message": "Job and related automations deleted successfully"}


@router.post("/{job_id}/publish")
async def publish_job(
    job_id: UUID,
    request: PublishJobRequest,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.publish))],
) -> dict[str, str]:
    """Publish a job to specific platforms."""
    stmt = select(JobRequirement).where(
        JobRequirement.id == job_id, JobRequirement.company_id == getattr(current_user, "company_id", None)
    )
    result = await session.execute(stmt)
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    for platform in request.platforms:
        stmt_posting = select(JobPosting).where(
            JobPosting.job_requirement_id == job_id, JobPosting.platform == platform
        )
        result_posting = await session.execute(stmt_posting)
        existing_posting = result_posting.scalar_one_or_none()

        if existing_posting:
            existing_posting.status = "PUBLISHED"
            existing_posting.posted_at = cast("Any", datetime.now())
        else:
            new_posting = JobPosting(
                job_requirement_id=job_id,
                platform=platform,
                status="PUBLISHED",
                posted_at=cast("Any", datetime.now()),
                company_id=job.company_id,
            )
            session.add(new_posting)

    await session.commit()

    # Notify Google Jobs if selected
    if "Google Jobs" in request.platforms:
        job_url = f"{settings.frontend_url}/jobs/{job_id}"
        await google_jobs_service.notify_job_update(job_url, update_type="URL_UPDATED")

    return {"message": f"Job published to {len(request.platforms)} platforms"}


@router.post("/generate-jd")
async def generate_jd_endpoint(
    request: JDGenerationRequest,
    _current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.generate))],
) -> dict[str, object]:
    """Generate or enhance a job description and optionally a workflow using AI."""
    jd_result = await generate_job_description_ai(
        title=request.title,
        existing_description=request.existing_description or "",
        location=request.location or "",
        experience_min=request.experience_min or "",
        experience_max=request.experience_max or "",
    )

    workflow: list[dict[str, object]] = []
    if request.generate_workflow:
        workflow = await hiring_agent_service.generate_automated_workflow(
            job_title=request.title, job_description=cast("str", jd_result.get("description", ""))
        )

    return {**jd_result, "suggested_workflow": workflow}


@router.post("/generate-workflow")
async def generate_workflow_endpoint(
    request: WorkflowGenerationRequest,
    _current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.generate))],
) -> list[dict[str, object]]:
    """Generate a structured automated workflow for a job."""
    workflow = await hiring_agent_service.generate_automated_workflow(
        job_title=request.title, job_description=request.description
    )
    return workflow
