from datetime import datetime
from typing import Annotated, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep, get_current_agent
from app.models.enterprise.user_role import EnterpriseUser as User
from app.models.enterprise.job import JobStatus, JobRequirement, JobPosting
from app.models.enterprise.assessment import AssessmentAutomation
from app.models.enterprise.communication import MailAutomation
from sqlalchemy import delete, func
from app.models.enterprise.user_role import EnterpriseUser as HiringAgent
from app.models.enterprise.candidate import CandidateApplication
from app.schemas.enterprise.jobs import JobRequirementCreate, JobRequirementResponse, PublishJobRequest, JobRequirementUpdate, JDGenerationRequest, WorkflowGenerationRequest, JobMetrics, JobStageResponse
from app.core.ai import generate_job_description_ai
from app.services.enterprise.hiring_agent import hiring_agent_service

router = APIRouter(prefix="/jobs", tags=["Enterprise Jobs"])

async def get_enterprise_agent(
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
) -> HiringAgent:
    """Ensure the user is an enterprise agent."""
    return current_agent

def normalize_workflow_stages(stages: List[dict]):
    """Ensure stage IDs are sequential strings 1, 2, 3..."""
    if not stages:
        return stages
    for i, stage in enumerate(stages):
        stage["id"] = str(i + 1)
    return stages

@router.post("/", response_model=JobRequirementResponse)
async def create_job(
    request: JobRequirementCreate,
    session: DBSessionDep
):
    """Create a new job requirement."""
    workflow_stages = normalize_workflow_stages(request.workflow_stages or [])
    new_job = JobRequirement(
        **request.model_dump(exclude={"target_platforms", "workflow_stages"}),
        workflow_stages=workflow_stages
    )
    session.add(new_job)
    await session.commit()
    await session.refresh(new_job)
    
    # Eager load for response
    stmt = select(JobRequirement).options(selectinload(JobRequirement.postings)).where(JobRequirement.id == new_job.id)
    result = await session.execute(stmt)
    return result.scalar_one()

@router.get("/", response_model=List[JobRequirementResponse])
async def list_jobs(
    session: DBSessionDep
):
    """List all jobs."""
    stmt = select(JobRequirement).options(
        selectinload(JobRequirement.postings), 
        selectinload(JobRequirement.company)
    ).where(
        JobRequirement.deleted_at == None
    ).order_by(JobRequirement.created_at.desc())
    
    result = await session.execute(stmt)
    jobs = result.scalars().all()
    return jobs

@router.get("/{job_id}", response_model=JobRequirementResponse)
async def get_job(
    job_id: UUID,
    session: DBSessionDep
):
    stmt = select(JobRequirement).options(
        selectinload(JobRequirement.postings), 
        selectinload(JobRequirement.company)
    ).where(
        JobRequirement.id == job_id
    )
    result = await session.execute(stmt)
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    # Calculate metrics
    # Stages mapping logic (simplified/dynamic)
    metrics_stmt = select(
        CandidateApplication.current_stage,
        func.count(CandidateApplication.id)
    ).where(
        CandidateApplication.job_requirement_id == job_id
    ).group_by(CandidateApplication.current_stage)
    
    metrics_result = await session.execute(metrics_stmt)
    counts = {stage: count for stage, count in metrics_result.all()}
    
    # Dynamic Stages (Rounds)
    stages_to_use = job.workflow_stages or []
    
    # Explicitly construct the response to ensure stages are included
    response = JobRequirementResponse.model_validate(job)
    
    response.stages = [
        JobStageResponse(
            id=int(s.get("id", i + 1)),
            name=s.get("name", f"Stage {i+1}"),
            count=counts.get(int(s.get("id", i + 1)), 0)
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
        onboarded=counts.get(5, 0)
    )
    
    return response

@router.patch("/{job_id}", response_model=JobRequirementResponse)
async def update_job(
    job_id: UUID,
    request: JobRequirementUpdate,
    session: DBSessionDep
):
    """Update an existing job requisition."""
    stmt = select(JobRequirement).where(
        JobRequirement.id == job_id
    )
    result = await session.execute(stmt)
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    update_data = request.model_dump(exclude_unset=True)
    if "workflow_stages" in update_data and update_data["workflow_stages"]:
        update_data["workflow_stages"] = normalize_workflow_stages(update_data["workflow_stages"])
        
    for key, value in update_data.items():
        setattr(job, key, value)
    
    await session.commit()
    await session.refresh(job)
    
    # Re-fetch with mappings
    stmt = select(JobRequirement).options(
        selectinload(JobRequirement.postings), 
        selectinload(JobRequirement.company)
    ).where(JobRequirement.id == job.id)
    result = await session.execute(stmt)
    return result.scalar_one()

@router.delete("/{job_id}")
async def delete_job(
    job_id: UUID,
    session: DBSessionDep
):
    """Soft delete a job requisition."""
    stmt = select(JobRequirement).where(
        JobRequirement.id == job_id
    )
    result = await session.execute(stmt)
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # 1. Hard-delete related automations
    await session.execute(
        delete(AssessmentAutomation).where(AssessmentAutomation.job_requirement_id == job_id)
    )
    await session.execute(
        delete(MailAutomation).where(MailAutomation.job_requirement_id == job_id)
    )

    # 2. Soft-delete the job requirement
    job.deleted_at = datetime.now()
    await session.commit()
    return {"message": "Job and related automations deleted successfully"}

@router.post("/{job_id}/publish")
async def publish_job(
    job_id: UUID,
    request: PublishJobRequest,
    session: DBSessionDep
):
    """Publish a job to specific platforms."""
    stmt = select(JobRequirement).where(
        JobRequirement.id == job_id
    )
    result = await session.execute(stmt)
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    for platform in request.platforms:
        stmt = select(JobPosting).where(
            JobPosting.job_requirement_id == job_id,
            JobPosting.platform == platform
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
                posted_at=datetime.now()
            )
            session.add(new_posting)
            
    await session.commit()
    return {"message": f"Job published to {len(request.platforms)} platforms"}

@router.post("/generate-jd")
async def generate_jd_endpoint(
    request: JDGenerationRequest
):
    """Generate or enhance a job description and optionally a workflow using AI."""
    jd_result = await generate_job_description_ai(
        title=request.title,
        existing_description=request.existing_description,
        location=request.location,
        experience_min=request.experience_min,
        experience_max=request.experience_max
    )
    
    workflow = []
    if request.generate_workflow:
        workflow = await hiring_agent_service.generate_automated_workflow(
            job_title=request.title,
            job_description=jd_result.get("description", "")
        )
    
    return {
        **jd_result,
        "suggested_workflow": workflow
    }

@router.post("/generate-workflow")
async def generate_workflow_endpoint(
    request: WorkflowGenerationRequest
):
    """Generate a structured automated workflow for a job."""
    workflow = await hiring_agent_service.generate_automated_workflow(
        job_title=request.title,
        job_description=request.description
    )
    return workflow
