from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import get_db
from app.models.enterprise.candidate import CandidateApplication
from app.models.enterprise.interview import InterviewAttempt, InterviewSchedule
from app.models.enterprise.user_role import EnterpriseUser
from app.services.enterprise.ai_interview_service import initialize_interview, process_interview_turn

router = APIRouter(prefix="/public/interview", tags=["Candidate AI Interview"])


@router.post("/verify")
async def verify_interview_session(
    application_id: UUID = Body(..., embed=True),
    email: str = Body(None, embed=True),
    db: AsyncSession = Depends(get_db),
):
    """
    Verify application and start/resume an interview session.
    """
    # 1. Get application with relations
    from app.models.enterprise.job import JobRequirement

    stmt = (
        select(CandidateApplication)
        .options(
            selectinload(CandidateApplication.candidate),
            selectinload(CandidateApplication.job_requirement).selectinload(JobRequirement.company),
        )
        .where(CandidateApplication.id == application_id)
    )
    result = await db.execute(stmt)
    application = result.scalar_one_or_none()

    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    # 2. Get Interview Schedule (Latest)
    stmt = (
        select(InterviewSchedule)
        .where(InterviewSchedule.application_id == application_id)
        .order_by(InterviewSchedule.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    schedule = result.scalar_one_or_none()

    if not schedule:
        raise HTTPException(status_code=404, detail="Interview schedule not found for this application.")

    # 3. Email verification if provided
    candidate_email = application.candidate.email if application.candidate else ""
    if email and candidate_email:
        if email.strip().lower() != candidate_email.strip().lower():
            raise HTTPException(
                status_code=403, detail="Email verification failed. Please use your registered email."
            )

    # 4. Identify a valid user_id for the attempt
    # Guest candidates might not have a user_id. We'll use the candidate's user_id if present,
    # or fallback to the interviewer/system user.
    user_id = application.candidate.user_id if application.candidate else None

    if not user_id:
        # Fallback 1: Use interviewer from schedule
        user_id = schedule.interviewer_id

    if not user_id:
        # Fallback 2: Find a system recruiter or first available user
        system_user_stmt = select(EnterpriseUser).limit(1)
        result = await db.execute(system_user_stmt)
        system_user = result.scalar_one_or_none()
        if system_user:
            user_id = system_user.id
        else:
            raise HTTPException(
                status_code=500,
                detail="System configuration error: No valid user found for interview session.",
            )

    try:
        data = await initialize_interview(db, str(schedule.id), str(user_id), candidate_email)

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
        raise HTTPException(status_code=500, detail=f"Initialization failed: {e!s}")


@router.post("/{attempt_id}/chat")
async def interview_chat_turn(
    attempt_id: UUID, text: str = Body(..., embed=True), db: AsyncSession = Depends(get_db)
):
    """
    Process a chat turn with the AI.
    """
    try:
        data = await process_interview_turn(db, str(attempt_id), text)
        return data
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{attempt_id}/complete")
async def complete_interview(
    attempt_id: UUID, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)
):
    """
    Mark interview as completed.
    """
    stmt = (
        select(InterviewAttempt)
        .options(selectinload(InterviewAttempt.schedule))
        .where(InterviewAttempt.id == attempt_id)
    )
    result = await db.execute(stmt)
    attempt = result.scalar_one_or_none()

    if not attempt:
        raise HTTPException(status_code=404, detail="Attempt not found")

    attempt.schedule.status = "COMPLETED"

    # Trigger next automation stage if applicable
    from app.services.enterprise.automation_service import trigger_automations

    application_id = attempt.schedule.application_id
    stmt = select(CandidateApplication).where(CandidateApplication.id == application_id)
    result = await db.execute(stmt)
    application = result.scalar_one_or_none()

    if application:
        # Move to next round and trigger
        application.current_stage += 1
        await trigger_automations(application.id, application.current_stage, db, background_tasks)

    await db.commit()
    return {"status": "SUCCESS", "message": "Interview completed and application moved to next stage."}
