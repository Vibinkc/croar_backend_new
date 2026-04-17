from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from app.core.ai import generate_job_description_ai
from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.assessment import AssessmentAutomation
from app.models.enterprise.candidate import CandidateApplication
from app.models.enterprise.communication import MailAutomation
from app.models.enterprise.job import JobPosting, JobRequirement
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
from app.services.enterprise.hiring_agent import hiring_agent_service

router = APIRouter(prefix="/jobs", tags=["Enterprise Jobs"])

# Removed get_enterprise_agent helper as it's redundant with PermissionChecker


def normalize_workflow_stages(stages: list[dict]):
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
    current_user: Annotated[Any, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.create))],
):
    """Create a new job requirement."""
    is_consultancy = getattr(current_user.company, "is_consultancy", False)
    target_company_id = request.company_id or current_user.company_id
    workflow_stages = normalize_workflow_stages(request.workflow_stages or [])

    # Validation: If consultancy, they can hire for partners.
    # If not, it must be their own company.
    if target_company_id != current_user.company_id:
        if not is_consultancy:
            raise HTTPException(status_code=403, detail="Not authorized to hire for other organizations.")
        # Verify it's a partner
        partner_stmt = select(Company).where(
            Company.id == target_company_id, Company.parent_id == current_user.company_id
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
        .options(selectinload(JobRequirement.postings))
        .where(JobRequirement.id == new_job.id)
    )
    result = await session.execute(stmt)
    return result.scalar_one()


@router.get("/", response_model=list[JobRequirementResponse])
async def list_jobs(
    session: DBSessionDep,
    current_user: Annotated[Any, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.read))],
    company_id: UUID | None = None,
):
    """List all jobs (optionally filtered by partner company)."""
    from sqlalchemy import or_

    from app.models.enterprise.company import Company

    is_consultancy = getattr(current_user.company, "is_consultancy", False)

    stmt = (
        select(JobRequirement)
        .options(selectinload(JobRequirement.postings), selectinload(JobRequirement.company))
        .where(JobRequirement.deleted_at == None)
    )

    if company_id:
        # User explicitly requested a specific company
        if company_id != current_user.company_id:
            if not is_consultancy:
                raise HTTPException(status_code=403, detail="Access denied.")
            # Verify partner
            partner_stmt = select(Company).where(
                Company.id == company_id, Company.parent_id == current_user.company_id
            )
            if not (await session.execute(partner_stmt)).scalar_one_or_none():
                raise HTTPException(status_code=403, detail="Invalid partner context.")
        stmt = stmt.where(JobRequirement.company_id == company_id)
    else:
        # Default view
        if is_consultancy:
            # Show jobs for the consultancy AND all its partners
            partner_ids_stmt = select(Company.id).where(Company.parent_id == current_user.company_id)
            partner_ids = (await session.execute(partner_ids_stmt)).scalars().all()
            stmt = stmt.where(
                or_(
                    JobRequirement.company_id == current_user.company_id,
                    JobRequirement.company_id.in_(partner_ids),
                )
            )
        else:
            stmt = stmt.where(JobRequirement.company_id == current_user.company_id)

    result = await session.execute(stmt.order_by(JobRequirement.created_at.desc()))
    jobs = result.scalars().all()
    return jobs


@router.get("/{job_id}", response_model=JobRequirementResponse)
async def get_job(
    job_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[Any, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.read))],
):
    from sqlalchemy import or_

    from app.models.enterprise.company import Company

    is_consultancy = getattr(current_user.company, "is_consultancy", False)

    stmt = (
        select(JobRequirement)
        .options(selectinload(JobRequirement.postings), selectinload(JobRequirement.company))
        .where(JobRequirement.id == job_id, JobRequirement.deleted_at == None)
    )

    if is_consultancy:
        # Allow if job belongs to consultancy OR any of its partners
        partner_ids_stmt = select(Company.id).where(Company.parent_id == current_user.company_id)
        partner_ids = (await session.execute(partner_ids_stmt)).scalars().all()
        stmt = stmt.where(
            or_(
                JobRequirement.company_id == current_user.company_id,
                JobRequirement.company_id.in_(partner_ids),
            )
        )
    else:
        stmt = stmt.where(JobRequirement.company_id == current_user.company_id)

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

    metrics_result = await session.execute(metrics_stmt)
    counts = {stage: count for stage, count in metrics_result.all()}

    # Dynamic Stages (Rounds)
    stages_to_use = job.workflow_stages or []

    # Explicitly construct the response to ensure stages are included
    response = JobRequirementResponse.model_validate(job)

    response.stages = [
        JobStageResponse(
            id=int(s.get("id", i + 1)),
            name=s.get("name", f"Stage {i + 1}"),
            count=counts.get(int(s.get("id", i + 1)), 0),
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
    current_user: Annotated[Any, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.update))],
):
    """Update an existing job requisition."""
    stmt = select(JobRequirement).where(
        JobRequirement.id == job_id, JobRequirement.company_id == current_user.company_id
    )
    result = await session.execute(stmt)
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    update_data = request.model_dump(exclude_unset=True)
    if update_data.get("workflow_stages"):
        update_data["workflow_stages"] = normalize_workflow_stages(update_data["workflow_stages"])

    for key, value in update_data.items():
        setattr(job, key, value)

    await session.commit()
    await session.refresh(job)

    # Re-fetch with mappings
    stmt = (
        select(JobRequirement)
        .options(selectinload(JobRequirement.postings), selectinload(JobRequirement.company))
        .where(JobRequirement.id == job_id, JobRequirement.company_id == current_user.company_id)
    )
    result = await session.execute(stmt)
    return result.scalar_one()


@router.delete("/{job_id}")
async def delete_job(
    job_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[Any, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.delete))],
):
    """Soft delete a job requisition."""
    stmt = select(JobRequirement).where(
        JobRequirement.id == job_id, JobRequirement.company_id == current_user.company_id
    )
    result = await session.execute(stmt)
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # 1. Hard-delete related automations
    await session.execute(
        delete(AssessmentAutomation).where(AssessmentAutomation.job_requirement_id == job_id)
    )
    await session.execute(delete(MailAutomation).where(MailAutomation.job_requirement_id == job_id))

    # 2. Soft-delete the job requirement
    job.deleted_at = datetime.now()
    await session.commit()
    return {"message": "Job and related automations deleted successfully"}


@router.post("/{job_id}/publish")
async def publish_job(
    job_id: UUID,
    request: PublishJobRequest,
    session: DBSessionDep,
    current_user: Annotated[Any, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.publish))],
):
    """Publish a job to specific platforms."""
    stmt = select(JobRequirement).where(
        JobRequirement.id == job_id, JobRequirement.company_id == current_user.company_id
    )
    result = await session.execute(stmt)
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    for platform in request.platforms:
        stmt = select(JobPosting).where(
            JobPosting.job_requirement_id == job_id, JobPosting.platform == platform
        )
        result = await session.execute(stmt)
        existing_posting = result.scalar_one_or_none()

        if existing_posting:
            existing_posting.status = "PUBLISHED"
            existing_posting.posted_at = datetime.now()
        else:
            new_posting = JobPosting(
                job_requirement_id=job_id,
                platform=platform,
                status="PUBLISHED",
                posted_at=datetime.now(),
                company_id=job.company_id,
            )
            session.add(new_posting)

    await session.commit()
    return {"message": f"Job published to {len(request.platforms)} platforms"}


@router.post("/generate-jd")
async def generate_jd_endpoint(
    request: JDGenerationRequest,
    current_user: Annotated[Any, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.generate))],
):
    """Generate or enhance a job description and optionally a workflow using AI."""
    jd_result = await generate_job_description_ai(
        title=request.title,
        existing_description=request.existing_description,
        location=request.location,
        experience_min=request.experience_min,
        experience_max=request.experience_max,
    )

    workflow = []
    if request.generate_workflow:
        workflow = await hiring_agent_service.generate_automated_workflow(
            job_title=request.title, job_description=jd_result.get("description", "")
        )

    return {**jd_result, "suggested_workflow": workflow}


@router.post("/generate-workflow")
async def generate_workflow_endpoint(
    request: WorkflowGenerationRequest,
    current_user: Annotated[Any, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.generate))],
):
    """Generate a structured automated workflow for a job."""
    workflow = await hiring_agent_service.generate_automated_workflow(
        job_title=request.title, job_description=request.description
    )
    return workflow
