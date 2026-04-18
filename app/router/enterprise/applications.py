from datetime import datetime
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.candidate import CandidateApplication
from app.models.enterprise.interview import InterviewSchedule
from app.models.shared.constants import ModuleScope, PermissionAction
from app.schemas.enterprise.applications import ApplicationResponse, UpdateStageRequest

router = APIRouter(prefix="/applications", tags=["Enterprise Applications"])


@router.get("/", response_model=list[ApplicationResponse])
async def list_applications(
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.read))],
    job_id: UUID | None = None,
) -> list[ApplicationResponse]:
    """List applications, optionally filtered by job_id."""
    company_id = getattr(current_user, "company_id", None)
    stmt = (
        select(CandidateApplication)
        .options(
            selectinload(CandidateApplication.candidate),
            selectinload(CandidateApplication.assessment_attempts),
            selectinload(CandidateApplication.onboarding),
            selectinload(CandidateApplication.interview_schedules).selectinload(InterviewSchedule.attempts),
        )
        .where(CandidateApplication.company_id == company_id)
    )

    if job_id:
        stmt = stmt.where(CandidateApplication.job_requirement_id == job_id)

    result = await session.execute(stmt)
    apps = cast("list[CandidateApplication]", result.scalars().all())

    final_apps: list[ApplicationResponse] = []
    for app in apps:
        # 1. Assessment Scores
        attempts = sorted(
            app.assessment_attempts,
            key=lambda x: (
                cast("datetime", x.completed_at).replace(tzinfo=None) if x.completed_at else datetime.min
            ),
            reverse=True,
        )
        latest = attempts[0] if attempts and attempts[0].status == "COMPLETED" else None

        # 2. AI Interview Score
        all_attempts = []
        for schedule in app.interview_schedules:
            all_attempts.extend(schedule.attempts)

        completed_interviews = [i for i in all_attempts if i.overall_score is not None]
        completed_interviews.sort(key=lambda x: cast("datetime", x.created_at), reverse=True)

        # 3. Populate onboarding_id
        onboarding_id = None
        if app.onboarding:
            onboarding_id = app.onboarding.id

        resp = ApplicationResponse.model_validate(app)
        if latest:
            resp.assessment_score = latest.score
            resp.aptitude_score = latest.aptitude_score
            resp.coding_score = latest.coding_score

        if completed_interviews:
            resp.ai_interview_score = float(completed_interviews[0].overall_score)

        resp.onboarding_id = onboarding_id
        final_apps.append(resp)

    return final_apps


@router.patch("/{application_id}/stage")
async def update_stage(
    application_id: UUID,
    request: UpdateStageRequest,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.moderate))],
) -> dict[str, object]:
    """Move application to a new stage."""
    company_id = getattr(current_user, "company_id", None)
    stmt = select(CandidateApplication).where(
        CandidateApplication.id == application_id, CandidateApplication.company_id == company_id
    )
    result = await session.execute(stmt)
    application = result.scalar_one_or_none()

    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    application.current_stage = request.new_stage
    await session.commit()

    from app.services.enterprise.automation_service import trigger_automations

    await trigger_automations(application.id, request.new_stage, session)
    await session.commit()

    return {"message": "Stage updated successfully", "new_stage": request.new_stage}


@router.get("/stages")
async def get_stages(
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.read))],
    job_id: UUID | None = None,
) -> list[Any]:
    """Return defined stages for the Kanban board."""
    if not job_id:
        return []

    try:
        from app.models.enterprise.job import JobRequirement

        company_id = getattr(current_user, "company_id", None)
        stmt = select(JobRequirement).where(
            JobRequirement.id == job_id, JobRequirement.company_id == company_id
        )
        result = await session.execute(stmt)
        job = result.scalar_one_or_none()

        if job and job.workflow_stages:
            return list(cast("list[Any]", job.workflow_stages))

    except Exception as e:
        print(f"Error in get_stages: {e}")

    return []


@router.delete("/bulk")
async def bulk_delete_applications(
    application_ids: list[UUID],
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.delete))],
) -> dict[str, object]:
    """Bulk delete applications."""
    from sqlalchemy import delete

    company_id = getattr(current_user, "company_id", None)
    stmt = delete(CandidateApplication).where(
        CandidateApplication.id.in_(application_ids), CandidateApplication.company_id == company_id
    )
    await session.execute(stmt)
    await session.commit()

    return {"message": f"Successfully deleted {len(application_ids)} applications"}
