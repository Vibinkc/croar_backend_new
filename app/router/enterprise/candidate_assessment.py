import uuid
from datetime import datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, HTTPException, Path
from sqlalchemy import select

from app.core.dependencies import DBSessionDep
from app.models.enterprise.assessment import AssessmentAttempt, AssessmentAutomation, AssessmentTemplate
from app.models.enterprise.candidate import Candidate, CandidateApplication
from app.schemas.enterprise.assessment import AssessmentAttemptResponse
from app.services.enterprise.ai_evaluator import ai_evaluator_service
from app.services.enterprise.automation_service import trigger_automations

router = APIRouter(prefix="/candidate-assessment", tags=["Candidate Assessment"])


@router.get("/{assessment_id}/start", response_model=AssessmentAttemptResponse)
async def start_assessment(
    assessment_id: Annotated[uuid.UUID, Path(...)],
    candidate_email: str,
    session: DBSessionDep,
    job_requirement_id: uuid.UUID | None = None,
) -> AssessmentAttempt:
    """Initialize or resume an assessment attempt for a candidate."""
    # 1. Verify Candidate
    stmt_cand = select(Candidate).where(Candidate.email == candidate_email)
    result_cand = await session.execute(stmt_cand)
    candidate = result_cand.scalar_one_or_none()

    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    # 2. Verify Assessment (could be automation or template)
    stmt_auto = select(AssessmentAutomation).where(AssessmentAutomation.id == assessment_id)
    automation = (await session.execute(stmt_auto)).scalar_one_or_none()

    template: AssessmentTemplate | None = None
    if not automation:
        stmt_tpl = select(AssessmentTemplate).where(AssessmentTemplate.id == assessment_id)
        template = (await session.execute(stmt_tpl)).scalar_one_or_none()

    if not automation and not template:
        raise HTTPException(status_code=404, detail="Assessment not found")

    # 3. Find/Verify Application context
    source_company_id = automation.company_id if automation else (template.company_id if template else None)

    application: CandidateApplication | None = None
    if job_requirement_id:
        stmt_app = select(CandidateApplication).where(
            CandidateApplication.candidate_id == candidate.id,
            CandidateApplication.job_requirement_id == job_requirement_id,
            CandidateApplication.company_id == source_company_id,
        )
        result_app = await session.execute(stmt_app)
        application = result_app.scalar_one_or_none()
        if not application:
            raise HTTPException(
                status_code=403, detail="You have not applied for the job associated with this test"
            )
    else:
        stmt_app_fallback = (
            select(CandidateApplication)
            .where(
                CandidateApplication.candidate_id == candidate.id,
                CandidateApplication.company_id == source_company_id,
            )
            .order_by(CandidateApplication.applied_at.desc())
        )
        result_app_fallback = await session.execute(stmt_app_fallback)
        application = result_app_fallback.scalars().first()
        if not application:
            raise HTTPException(
                status_code=403, detail="No application found for this candidate in this organization"
            )

    # 4. Check for existing attempt
    if automation:
        stmt_attempt = select(AssessmentAttempt).where(
            AssessmentAttempt.automation_id == automation.id, AssessmentAttempt.candidate_id == candidate.id
        )
    else:
        tpl_id = template.id if template else None
        app_id = application.id if application else None
        stmt_attempt = select(AssessmentAttempt).where(
            AssessmentAttempt.template_id == tpl_id,
            AssessmentAttempt.candidate_id == candidate.id,
            AssessmentAttempt.application_id == app_id,
        )
    result_attempt = await session.execute(stmt_attempt)
    attempt = result_attempt.scalar_one_or_none()

    if not attempt:
        attempt = AssessmentAttempt(
            automation_id=automation.id if automation else None,
            template_id=template.id if template else None,
            candidate_id=candidate.id,
            application_id=application.id,
            company_id=source_company_id,
            status="STARTED",
            started_at=datetime.now(),
        )
        session.add(attempt)
        await session.commit()
        await session.refresh(attempt)

    return attempt


@router.post("/{attempt_id}/submit")
async def submit_assessment(
    attempt_id: uuid.UUID, answers: dict[str, Any], session: DBSessionDep
) -> dict[str, Any]:
    """Submit assessment answers and trigger AI evaluation."""
    stmt = select(AssessmentAttempt).where(AssessmentAttempt.id == attempt_id)
    result = await session.execute(stmt)
    attempt = result.scalar_one_or_none()

    if not attempt or attempt.status == "COMPLETED":
        raise HTTPException(status_code=404, detail="Attempt not found or already completed")

    # Fetch Questions from template or automation
    questions: list[dict[str, Any]] = []
    if attempt.automation_id:
        stmt_auto = select(AssessmentAutomation).where(AssessmentAutomation.id == attempt.automation_id)
        automation = (await session.execute(stmt_auto)).scalar_one_or_none()
        questions = cast("list[dict[str, Any]]", (automation.generated_questions if automation else []) or [])
    else:
        stmt_tpl = select(AssessmentTemplate).where(AssessmentTemplate.id == attempt.template_id)
        template = (await session.execute(stmt_tpl)).scalar_one_or_none()
        questions = cast("list[dict[str, Any]]", (template.generated_questions if template else []) or [])

    # Evaluate each answer (simulated for MCQs, AI for others)
    apt_correct = 0
    apt_total = 0
    cod_score_accum = 0.0
    cod_total = 0

    for q in questions:
        q_id = str(q.get("id"))
        # Generated questions are typed "APTITUDE"/"CODING"; also accept legacy "mcq"/"code".
        q_type = str(q.get("type") or "").upper()
        ans_val = answers.get(q_id)

        if q_type in ("APTITUDE", "MCQ"):
            apt_total += 1
            if str(ans_val) == str(q.get("correct_answer")):
                apt_correct += 1
        elif q_type in ("CODING", "CODE"):
            cod_total += 1
            problem = q.get("problem_statement") or q.get("text") or q.get("question") or ""
            content = q.get("content") if isinstance(q.get("content"), dict) else {}
            test_cases = (content or {}).get("test_cases") or q.get("test_cases", [])
            evaluation = await ai_evaluator_service.evaluate_code_response(
                str(problem), cast("list[dict[str, str]]", test_cases or []), str(ans_val)
            )
            try:
                cod_score_accum += float(evaluation.get("score", 0))
            except (TypeError, ValueError):
                pass

    apt_score = int((apt_correct / apt_total) * 100) if apt_total > 0 else None
    cod_score = int(cod_score_accum / cod_total) if cod_total > 0 else None

    scores_to_average = []
    if apt_score is not None:
        scores_to_average.append(apt_score)
    if cod_score is not None:
        scores_to_average.append(cod_score)

    overall_score = sum(scores_to_average) // len(scores_to_average) if scores_to_average else 0

    attempt.answers = answers
    attempt.score = overall_score
    attempt.aptitude_score = apt_score
    attempt.coding_score = cod_score
    attempt.status = "COMPLETED"
    attempt.completed_at = cast("Any", datetime.now())

    # Update Match Score in Application
    stmt_app = select(CandidateApplication).where(CandidateApplication.id == attempt.application_id)
    res_app = await session.execute(stmt_app)
    application = res_app.scalar_one_or_none()

    if application:
        application.ai_match_score = cast(
            "Any", (float(application.ai_match_score or 0) + float(overall_score)) / 2
        )
        # Flush to allow trigger_automations to see it
        await session.flush()

        # Trigger next stage automations if overall score is good
        if overall_score >= 60:
            await trigger_automations(application.id, application.current_stage, session)

    await session.commit()
    return {"status": "success", "score": overall_score}


@router.get("/my-assessments")
async def get_my_assessments(candidate_email: str, session: DBSessionDep) -> list[dict[str, Any]]:
    """List all assessments (pending and completed) for a candidate."""
    stmt_cand = select(Candidate).where(Candidate.email == candidate_email)
    res_cand = await session.execute(stmt_cand)
    candidate = res_cand.scalar_one_or_none()

    if not candidate:
        return []

    stmt = (
        select(AssessmentAttempt)
        .where(AssessmentAttempt.candidate_id == candidate.id)
        .order_by(AssessmentAttempt.started_at.desc())
    )
    result = await session.execute(stmt)
    attempts = result.scalars().all()

    return [
        {
            "id": str(a.id),
            "status": a.status,
            "score": a.score,
            "started_at": a.started_at,
            "completed_at": a.completed_at,
            "topic": "Assessment",  # Could fetch from template/automation
        }
        for a in attempts
    ]
