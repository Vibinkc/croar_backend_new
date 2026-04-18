from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep
from app.models.enterprise.candidate import CandidateApplication
from app.models.enterprise.interview import InterviewAttempt, InterviewSchedule
from app.models.enterprise.user_role import EnterpriseUser
from app.services.enterprise.ai_interview_service import initialize_interview, process_interview_turn

router = APIRouter(prefix="/public/interview", tags=["Candidate AI Interview"])


@router.post("/verify")
async def verify_interview_session(
    session: DBSessionDep, application_id: UUID = Body(..., embed=True), email: str = Body(None, embed=True)
) -> dict[str, Any]:
    """
    Verify application and start/resume an interview session.
    """
    from app.models.enterprise.job import JobRequirement

    stmt = (
        select(CandidateApplication)
        .options(
            selectinload(CandidateApplication.candidate),
            selectinload(CandidateApplication.job_requirement).selectinload(JobRequirement.company),
        )
        .where(CandidateApplication.id == application_id)
    )
    result = await session.execute(stmt)
    application = result.scalar_one_or_none()

    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    stmt_s = (
        select(InterviewSchedule)
        .where(InterviewSchedule.application_id == application_id)
        .order_by(InterviewSchedule.created_at.desc())
        .limit(1)
    )
    result_s = await session.execute(stmt_s)
    schedule = result_s.scalar_one_or_none()

    if not schedule:
        raise HTTPException(status_code=404, detail="Interview schedule not found for this application.")

    candidate_email = application.candidate.email if application.candidate else ""
    if email and candidate_email:
        if email.strip().lower() != candidate_email.strip().lower():
            raise HTTPException(
                status_code=403, detail="Email verification failed. Please use your registered email."
            )

    user_id = application.candidate.user_id if application.candidate else None

    if not user_id:
        user_id = schedule.interviewer_id

    if not user_id:
        system_user_stmt = select(EnterpriseUser).limit(1)
        res_sys = await session.execute(system_user_stmt)
        system_user = res_sys.scalar_one_or_none()
        if system_user:
            user_id = system_user.id
        else:
            raise HTTPException(
                status_code=500,
                detail="System configuration error: No valid user found for interview session.",
            )

    try:
        data = await initialize_interview(session, str(schedule.id), str(user_id), candidate_email)

        company = application.job_requirement.company if application.job_requirement else None

        return {
            "application_id": str(application_id),
            "candidate_name": application.candidate.full_name if application.candidate else "Candidate",
            "job_title": application.job_requirement.title if application.job_requirement else "Position",
            "organization": {
                "name": company.name if company else "Our Company",
                "logo_url": company.logo_url if company else None,
            },
            **data,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Initialization failed: {e!s}") from e


@router.post("/{attempt_id}/chat")
async def interview_chat_turn(
    session: DBSessionDep, attempt_id: UUID, text: str = Body(..., embed=True)
) -> dict[str, Any]:
    """
    Process a chat turn with the AI.
    """
    try:
        data = await process_interview_turn(session, str(attempt_id), text)
        return cast("dict[str, Any]", data)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/{attempt_id}/complete")
async def complete_interview(
    session: DBSessionDep, attempt_id: UUID, background_tasks: BackgroundTasks
) -> dict[str, str]:
    """
    Mark interview as completed.
    """
    stmt = (
        select(InterviewAttempt)
        .options(selectinload(InterviewAttempt.schedule))
        .where(InterviewAttempt.id == attempt_id)
    )
    result = await session.execute(stmt)
    attempt = result.scalar_one_or_none()

    if not attempt:
        raise HTTPException(status_code=404, detail="Attempt not found")

    if attempt.schedule:
        attempt.schedule.status = "COMPLETED"

        from app.services.enterprise.automation_service import trigger_automations

        application_id = attempt.schedule.application_id
        stmt_app = select(CandidateApplication).where(CandidateApplication.id == application_id)
        res_app = await session.execute(stmt_app)
        application = res_app.scalar_one_or_none()

        if application:
            application.current_stage += 1
            await trigger_automations(application.id, application.current_stage, session, background_tasks)

    await session.commit()
    return {"status": "SUCCESS", "message": "Interview completed and application moved to next stage."}
