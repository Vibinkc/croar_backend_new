from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db
from app.models.enterprise.assessment import AssessmentAttempt, AssessmentAutomation, AssessmentTemplate
from app.models.enterprise.candidate import Candidate, CandidateApplication
from app.schemas.enterprise.assessment import AssessmentAttemptCreate, AssessmentAttemptResponse

router = APIRouter(prefix="/public/assessment", tags=["Candidate Assessment"])


@router.post("/verify", response_model=AssessmentAttemptResponse)
async def verify_candidate_and_start(req: AssessmentAttemptCreate, db: AsyncSession = Depends(get_db)):
    """
    Verify candidate email and start a test attempt.
    """
    # 1. Get automation or template
    automation = None
    template = None
    job_requirement_id = None

    if req.automation_id:
        stmt = select(AssessmentAutomation).where(AssessmentAutomation.id == req.automation_id)
        result = await db.execute(stmt)
        automation = result.scalar_one_or_none()
        if automation:
            job_requirement_id = automation.job_requirement_id

    if not automation and req.template_id:
        stmt = select(AssessmentTemplate).where(AssessmentTemplate.id == req.template_id)
        result = await db.execute(stmt)
        template = result.scalar_one_or_none()
        # For template-based manual sends, we might not have a job_requirement_id on the template itself
        # but the request should probably specify it if we want to verify application
        # However, AssessmentTemplate doesn't have job_requirement_id

    if not automation and not template:
        raise HTTPException(status_code=404, detail="Assessment or Template not found")

    # 2. Get candidate by email
    stmt = select(Candidate).where(Candidate.email == req.email)
    result = await db.execute(stmt)
    candidate = result.scalar_one_or_none()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found with this email")

    # 3. Verify application for this job (if job_requirement_id is available)
    application = None
    source_company_id = automation.company_id if automation else template.company_id
    if job_requirement_id:
        stmt = select(CandidateApplication).where(
            CandidateApplication.candidate_id == candidate.id,
            CandidateApplication.job_requirement_id == job_requirement_id,
            CandidateApplication.company_id == source_company_id,
        )
        result = await db.execute(stmt)
        application = result.scalar_one_or_none()
        if not application:
            raise HTTPException(
                status_code=403, detail="You have not applied for the job associated with this test"
            )
    else:
        # Fallback to the most recent application for THIS company
        stmt = (
            select(CandidateApplication)
            .where(
                CandidateApplication.candidate_id == candidate.id,
                CandidateApplication.company_id == source_company_id,
            )
            .order_by(CandidateApplication.applied_at.desc())
        )
        result = await db.execute(stmt)
        application = result.scalars().first()
        if not application:
            raise HTTPException(
                status_code=403, detail="No application found for this candidate in this organization"
            )

    # 4. Create or return existing attempt
    if automation:
        stmt = select(AssessmentAttempt).where(
            AssessmentAttempt.automation_id == automation.id, AssessmentAttempt.candidate_id == candidate.id
        )
    else:
        stmt = select(AssessmentAttempt).where(
            AssessmentAttempt.template_id == template.id,
            AssessmentAttempt.candidate_id == candidate.id,
            AssessmentAttempt.application_id == application.id,
        )
    result = await db.execute(stmt)
    attempt = result.scalar_one_or_none()

    if not attempt:
        attempt = AssessmentAttempt(
            automation_id=automation.id if automation else None,
            template_id=template.id if template else None,
            candidate_id=candidate.id,
            application_id=application.id,
            company_id=source_company_id,
            status="STARTED",
        )
        db.add(attempt)
        await db.commit()
    await db.refresh(attempt)

    # Add metadata for UI
    source = automation or template
    attempt_res = AssessmentAttemptResponse.model_validate(attempt)
    attempt_res.topic = source.topic
    attempt_res.type = source.type

    return attempt_res


@router.get("/{attempt_id}")
async def get_test_data(attempt_id: UUID, db: AsyncSession = Depends(get_db)):
    """Get the questions for the test (stripping correct answers)."""
    # Load attempt with application, job requirement and company
    from sqlalchemy.orm import selectinload

    from app.models.enterprise.job import JobRequirement

    stmt = (
        select(AssessmentAttempt)
        .options(
            selectinload(AssessmentAttempt.application)
            .selectinload(CandidateApplication.job_requirement)
            .selectinload(JobRequirement.company)
        )
        .where(AssessmentAttempt.id == attempt_id)
    )
    result = await db.execute(stmt)
    attempt = result.scalar_one_or_none()

    if not attempt:
        raise HTTPException(status_code=404, detail="Attempt not found")

    automation = None
    template = None
    if attempt.automation_id:
        stmt = select(AssessmentAutomation).where(AssessmentAutomation.id == attempt.automation_id)
        result = await db.execute(stmt)
        automation = result.scalar_one_or_none()
    elif attempt.template_id:
        stmt = select(AssessmentTemplate).where(AssessmentTemplate.id == attempt.template_id)
        result = await db.execute(stmt)
        template = result.scalar_one_or_none()

    source = automation or template
    if not source:
        raise HTTPException(status_code=404, detail="Assessment source not found")

    # Strip correct answers for MCQs
    questions = []
    if source.generated_questions:
        for q in source.generated_questions:
            q_copy = q.copy()
            if "correct_answer" in q_copy:
                del q_copy["correct_answer"]
            if "answer" in q_copy:
                del q_copy["answer"]
            questions.append(q_copy)

    company = None
    if (
        attempt.application
        and attempt.application.job_requirement
        and attempt.application.job_requirement.company
    ):
        company = attempt.application.job_requirement.company

    return {
        "id": attempt_id,
        "type": source.type,
        "topic": source.topic,
        "duration": source.test_duration,
        "questions": questions,
        "status": attempt.status,
        "organization": {
            "name": company.name if company else "Our Company",
            "logo_url": company.logo_url if company else None,
        },
    }


@router.post("/{attempt_id}/submit")
async def submit_test(attempt_id: UUID, answers: dict[str, Any], db: AsyncSession = Depends(get_db)):
    """Submit answers and calculate score."""
    stmt = select(AssessmentAttempt).where(AssessmentAttempt.id == attempt_id)
    result = await db.execute(stmt)
    attempt = result.scalar_one_or_none()

    if not attempt or attempt.status == "COMPLETED":
        raise HTTPException(status_code=400, detail="Invalid attempt or already completed")

    source = None
    automation = None
    if attempt.automation_id:
        stmt = select(AssessmentAutomation).where(AssessmentAutomation.id == attempt.automation_id)
        result = await db.execute(stmt)
        automation = source = result.scalar_one_or_none()
    elif attempt.template_id:
        stmt = select(AssessmentTemplate).where(AssessmentTemplate.id == attempt.template_id)
        result = await db.execute(stmt)
        source = result.scalar_one_or_none()

    if not source:
        raise HTTPException(status_code=404, detail="Source template not found")

    # Dual scoring logic
    apt_correct = 0
    apt_total = 0
    cod_score_accum = 0
    cod_total = 0

    questions = source.generated_questions or []
    for q in questions:
        q_id = str(q["id"])
        q_type = q.get("type", "APTITUDE")  # Default to APTITUDE for legacy

        if q_type == "APTITUDE":
            apt_total += 1
            correct_val = q.get("correct_answer") or q.get("answer")
            if answers.get(q_id) == correct_val:
                apt_correct += 1
        elif q_type == "CODING":
            cod_total += 1
            # Mock coding score: if code exists and is reasonably long, give points
            code = answers.get(q_id, "")
            if len(code) > 100:
                cod_score_accum += 100
            elif len(code) > 50:
                cod_score_accum += 80
            elif len(code) > 10:
                cod_score_accum += 40
            else:
                cod_score_accum += 0

    apt_score = int((apt_correct / apt_total) * 100) if apt_total > 0 else None
    cod_score = int(cod_score_accum / cod_total) if cod_total > 0 else None

    # Overall score (simple average of percentages)
    scores_to_average = [s for s in [apt_score, cod_score] if s is not None]
    if scores_to_average:
        overall_score = sum(scores_to_average) // len(scores_to_average)
    else:
        # If no questions of any type, should ideally not happen but default to 0
        overall_score = 0

    attempt.answers = answers
    attempt.score = overall_score
    attempt.aptitude_score = apt_score
    attempt.coding_score = cod_score
    attempt.status = "COMPLETED"
    attempt.completed_at = datetime.utcnow()

    # 1. Update overall application score
    stmt = select(CandidateApplication).where(CandidateApplication.id == attempt.application_id)
    result = await db.execute(stmt)
    application = result.scalar_one_or_none()

    if application:
        # 2. Evaluate if candidate should move to next round
        context = {
            "ai_match_score": float(application.ai_match_score or 0),
            "assessment_score": float(overall_score),
            "current_stage": application.current_stage,
            "topic": source.topic,
        }

        # Only evaluate criteria if it was an automation trigger
        should_move = False
        if automation:
            from app.services.enterprise.automation_service import evaluate_criteria

            should_move = await evaluate_criteria(automation.criteria, context)

        if should_move:
            # Move to next stage (assuming sequential stages)
            application.current_stage += 1
            await db.flush()  # Ensure update is visible

            # 3. Trigger automations for the new stage
            from app.services.enterprise.automation_service import trigger_automations

            await trigger_automations(application.id, application.current_stage, db)

    await db.commit()
    return {
        "score": overall_score,
        "status": "COMPLETED",
        "moved": should_move if "should_move" in locals() else False,
    }
