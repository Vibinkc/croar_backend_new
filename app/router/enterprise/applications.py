from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.candidate import CandidateApplication
from app.models.enterprise.interview import InterviewSchedule  # Added
from app.models.shared.constants import ModuleScope, PermissionAction
from app.schemas.enterprise.applications import ApplicationResponse, UpdateStageRequest

router = APIRouter(prefix="/applications", tags=["Enterprise Applications"])

# Removed redundant helper


@router.get("/", response_model=list[ApplicationResponse])
async def list_applications(
    session: DBSessionDep,
    current_user: Annotated[Any, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.read))],
    job_id: UUID = None,
):
    """List applications, optionally filtered by job_id."""
    stmt = (
        select(CandidateApplication)
        .options(
            selectinload(CandidateApplication.candidate),
            selectinload(CandidateApplication.assessment_attempts),
            selectinload(CandidateApplication.onboarding),
            selectinload(CandidateApplication.interview_schedules).selectinload(InterviewSchedule.attempts),
        )
        .where(CandidateApplication.company_id == current_user.company_id)
    )

    if job_id:
        stmt = stmt.where(CandidateApplication.job_requirement_id == job_id)

    result = await session.execute(stmt)
    apps = result.scalars().all()

    # Populate scores for each app
    for app in apps:
        # 1. Assessment Scores
        attempts = sorted(
            app.assessment_attempts,
            key=lambda x: x.completed_at.replace(tzinfo=None) if x.completed_at else datetime.min,
            reverse=True,
        )
        latest = attempts[0] if attempts and attempts[0].status == "COMPLETED" else None

        if latest:
            app.assessment_score = latest.score
            app.aptitude_score = latest.aptitude_score
            app.coding_score = latest.coding_score
        else:
            app.assessment_score = None
            app.aptitude_score = None
            app.coding_score = None

        # 2. AI Interview Score
        all_interviews = []
        for schedule in app.interview_schedules:
            all_interviews.extend(schedule.attempts)

        # Sort by created_at to get the latest
        completed_interviews = [i for i in all_interviews if i.overall_score is not None]
        completed_interviews.sort(key=lambda x: x.created_at, reverse=True)

        if completed_interviews:
            app.ai_interview_score = float(completed_interviews[0].overall_score)
        else:
            app.ai_interview_score = None

        # 3. Populate onboarding_id
        if app.onboarding:
            if isinstance(app.onboarding, list) and len(app.onboarding) > 0:
                app.onboarding_id = app.onboarding[0].id
            elif not isinstance(app.onboarding, list):
                app.onboarding_id = app.onboarding.id

    return apps


@router.patch("/{application_id}/stage")
async def update_stage(
    application_id: UUID,
    request: UpdateStageRequest,
    session: DBSessionDep,
    current_user: Annotated[Any, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.moderate))],
):
    """Move application to a new stage."""
    stmt = select(CandidateApplication).where(
        CandidateApplication.id == application_id, CandidateApplication.company_id == current_user.company_id
    )
    result = await session.execute(stmt)
    application = result.scalar_one_or_none()

    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    application.current_stage = request.new_stage
    await session.commit()

    # Trigger Mail Automation for the new stage
    from app.services.enterprise.automation_service import trigger_automations

    await trigger_automations(application.id, request.new_stage, session)
    await session.commit()  # Save any creations triggered by automations!

    return {"message": "Stage updated successfully", "new_stage": request.new_stage}


@router.get("/stages")
async def get_stages(
    session: DBSessionDep,
    current_user: Annotated[Any, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.read))],
    job_id: UUID = None,
):
    """Return defined stages for the Kanban board."""

    if not job_id:
        return []

    try:
        from app.models.enterprise.job import JobRequirement

        stmt = select(JobRequirement).where(
            JobRequirement.id == job_id, JobRequirement.company_id == current_user.company_id
        )
        result = await session.execute(stmt)
        job = result.scalar_one_or_none()

        if job and job.workflow_stages:
            return job.workflow_stages

    except Exception as e:
        print(f"Error in get_stages: {e}")

    return []


@router.delete("/bulk")
async def bulk_delete_applications(
    application_ids: list[UUID],
    session: DBSessionDep,
    current_user: Annotated[Any, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.delete))],
):
    """Bulk delete applications."""
    from sqlalchemy import delete

    stmt = delete(CandidateApplication).where(
        CandidateApplication.id.in_(application_ids),
        CandidateApplication.company_id == current_user.company_id,
    )
    await session.execute(stmt)
    await session.commit()

    return {"message": f"Successfully deleted {len(application_ids)} applications"}
