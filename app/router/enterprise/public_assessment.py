"""Public (candidate-facing) assessment-taking endpoints.

The candidate take page (frontend /assessment/take/[id]) drives this flow:
  1. POST /public/assessment/verify   -> identify candidate by email, create/resume attempt
  2. GET  /public/assessment/{id}      -> fetch the questions (without answers) + metadata
  3. POST /public/assessment/{id}/submit -> grade and store

`id` in the take URL is the assessment TEMPLATE id (or an automation id); the
frontend sends both as automation_id/template_id and we resolve whichever exists.
"""

import os
import uuid
from datetime import UTC, datetime
from typing import Any, cast

import aiofiles
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.dependencies import DBSessionDep
from app.models.enterprise.assessment import AssessmentAttempt, AssessmentAutomation, AssessmentTemplate
from app.models.enterprise.candidate import Candidate, CandidateApplication
from app.models.enterprise.company import Company
from app.services.enterprise.ai_evaluator import ai_evaluator_service
from app.services.enterprise.automation_service import trigger_automations

router = APIRouter(prefix="/public/assessment", tags=["Public Assessment"])


def _utcnow() -> datetime:
    """The naive UTC timestamp datetime.utcnow() used to return, without the deprecated call.

    AssessmentAttempt.started_at is a plain DateTime column, so the value must stay naive -
    handing SQLAlchemy an aware datetime here would change what gets stored.
    """
    return datetime.now(UTC).replace(tzinfo=None)


class VerifyRequest(BaseModel):
    email: str
    automation_id: uuid.UUID | None = None
    template_id: uuid.UUID | None = None


async def _resolve_source(
    session: Any, id_val: uuid.UUID
) -> tuple[AssessmentAutomation | None, AssessmentTemplate | None]:
    """Resolve an id to either an automation or a template (whichever exists)."""
    automation = (
        await session.execute(select(AssessmentAutomation).where(AssessmentAutomation.id == id_val))
    ).scalar_one_or_none()
    template: AssessmentTemplate | None = None
    if not automation:
        template = (
            await session.execute(select(AssessmentTemplate).where(AssessmentTemplate.id == id_val))
        ).scalar_one_or_none()
    return automation, template


@router.post("/verify")
async def verify_assessment(request: VerifyRequest, session: DBSessionDep) -> dict[str, Any]:
    """Verify the candidate's email and create (or resume) their assessment attempt."""
    id_val = request.automation_id or request.template_id
    if not id_val:
        raise HTTPException(status_code=400, detail="Missing assessment identifier.")

    email = (request.email or "").strip().lower()
    candidate = (
        await session.execute(select(Candidate).where(func.lower(Candidate.email) == email))
    ).scalar_one_or_none()
    if not candidate:
        raise HTTPException(
            status_code=404,
            detail="We couldn't find an application with this email. Please use the email you applied with.",
        )

    automation, template = await _resolve_source(session, id_val)
    if not automation and not template:
        raise HTTPException(status_code=404, detail="This assessment no longer exists.")

    company_id = automation.company_id if automation else (template.company_id if template else None)

    # Find the candidate's application within this organization (most recent).
    application = (
        (
            await session.execute(
                select(CandidateApplication)
                .where(
                    CandidateApplication.candidate_id == candidate.id,
                    CandidateApplication.company_id == company_id,
                )
                .order_by(CandidateApplication.applied_at.desc())
            )
        )
        .scalars()
        .first()
    )
    if not application:
        raise HTTPException(
            status_code=403, detail="No application found for this candidate in this organization."
        )

    # Reuse an existing attempt if one is in progress / completed.
    if automation:
        attempt = (
            await session.execute(
                select(AssessmentAttempt).where(
                    AssessmentAttempt.automation_id == automation.id,
                    AssessmentAttempt.candidate_id == candidate.id,
                )
            )
        ).scalar_one_or_none()
    else:
        attempt = (
            await session.execute(
                select(AssessmentAttempt).where(
                    AssessmentAttempt.template_id == (template.id if template else None),
                    AssessmentAttempt.candidate_id == candidate.id,
                    AssessmentAttempt.application_id == application.id,
                )
            )
        ).scalar_one_or_none()

    if not attempt:
        attempt = AssessmentAttempt(
            automation_id=automation.id if automation else None,
            template_id=template.id if template else None,
            candidate_id=candidate.id,
            application_id=application.id,
            company_id=company_id,
            status="STARTED",
            started_at=_utcnow(),
        )
        session.add(attempt)
        await session.commit()
        await session.refresh(attempt)

    # Return the source metadata too, so the candidate's "Ready to start" intro screen can show
    # the real question count / topic / round type (the frontend reads these off this response).
    source = automation or template
    src_type = (
        source.type.value
        if source and hasattr(source.type, "value")
        else str(source.type)
        if source
        else None
    )
    # Strip correct answers before exposing questions to the candidate.
    src_questions = (
        [
            {k: v for k, v in q.items() if k != "correct_answer"}
            for q in (source.generated_questions or [])
            if isinstance(q, dict)
        ]
        if source
        else []
    )
    return {
        "id": str(attempt.id),
        "status": attempt.status,
        "score": attempt.score,
        "type": src_type,
        "topic": source.topic if source else None,
        "duration": source.test_duration if source else None,
        "generated_questions": src_questions,
    }


@router.get("/{attempt_id}")
async def get_assessment(attempt_id: uuid.UUID, session: DBSessionDep) -> dict[str, Any]:
    """Return the questions + metadata for an attempt (correct answers stripped)."""
    attempt = (
        await session.execute(select(AssessmentAttempt).where(AssessmentAttempt.id == attempt_id))
    ).scalar_one_or_none()
    if not attempt:
        raise HTTPException(status_code=404, detail="Assessment attempt not found.")

    source: AssessmentAutomation | AssessmentTemplate | None
    if attempt.automation_id:
        source = (
            await session.execute(
                select(AssessmentAutomation).where(AssessmentAutomation.id == attempt.automation_id)
            )
        ).scalar_one_or_none()
    else:
        source = (
            await session.execute(
                select(AssessmentTemplate).where(AssessmentTemplate.id == attempt.template_id)
            )
        ).scalar_one_or_none()

    if not source:
        raise HTTPException(status_code=404, detail="Assessment definition not found.")

    questions = cast("list[dict[str, Any]]", source.generated_questions or [])
    # Never expose the correct answer to the candidate.
    safe_questions = [{k: v for k, v in q.items() if k != "correct_answer"} for q in questions]

    organization: dict[str, Any] | None = None
    if attempt.company_id:
        company = (
            await session.execute(select(Company).where(Company.id == attempt.company_id))
        ).scalar_one_or_none()
        if company:
            organization = {"name": company.name, "logo_url": getattr(company, "logo_url", None)}

    type_val = source.type.value if hasattr(source.type, "value") else str(source.type)

    return {
        "id": str(attempt.id),
        "duration": source.test_duration,
        "type": type_val,
        "topic": source.topic,
        "questions": safe_questions,
        "organization": organization,
    }


@router.post("/{attempt_id}/submit")
async def submit_assessment(
    attempt_id: uuid.UUID, answers: dict[str, Any], session: DBSessionDep
) -> dict[str, Any]:
    """Grade the submitted answers and store the result."""
    attempt = (
        await session.execute(select(AssessmentAttempt).where(AssessmentAttempt.id == attempt_id))
    ).scalar_one_or_none()
    if not attempt or attempt.status == "COMPLETED":
        raise HTTPException(status_code=404, detail="Attempt not found or already completed.")

    # Fetch the source questions (with answers) for grading.
    questions: list[dict[str, Any]] = []
    if attempt.automation_id:
        automation = (
            await session.execute(
                select(AssessmentAutomation).where(AssessmentAutomation.id == attempt.automation_id)
            )
        ).scalar_one_or_none()
        questions = cast("list[dict[str, Any]]", (automation.generated_questions if automation else []) or [])
    else:
        template = (
            await session.execute(
                select(AssessmentTemplate).where(AssessmentTemplate.id == attempt.template_id)
            )
        ).scalar_one_or_none()
        questions = cast("list[dict[str, Any]]", (template.generated_questions if template else []) or [])

    apt_correct = 0
    apt_total = 0
    cod_score_accum = 0.0
    cod_total = 0

    for q in questions:
        q_id = str(q.get("id"))
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

    scores_to_average = [s for s in (apt_score, cod_score) if s is not None]
    overall_score = sum(scores_to_average) // len(scores_to_average) if scores_to_average else 0

    # A VIDEO assessment can't be auto-graded — the candidate recorded answers a human reviews.
    # Mark it pending review; scoring + stage-advance happen when HR scores it.
    is_video = any(str(q.get("type") or "").upper() == "VIDEO" for q in questions)

    attempt.answers = answers
    attempt.completed_at = cast("Any", datetime.now())
    if is_video:
        attempt.score = None
        attempt.status = "PENDING_REVIEW"
    else:
        attempt.score = overall_score
        attempt.aptitude_score = apt_score
        attempt.coding_score = cod_score
        attempt.status = "COMPLETED"

    application = (
        await session.execute(
            select(CandidateApplication).where(CandidateApplication.id == attempt.application_id)
        )
    ).scalar_one_or_none()
    if application and not is_video:
        application.ai_match_score = cast(
            "Any", (float(application.ai_match_score or 0) + float(overall_score)) / 2
        )
        await session.flush()
        if overall_score >= 60:
            await trigger_automations(application.id, application.current_stage, session)

    # Meter one assessment credit per completed attempt (company derived from the application).
    if application and getattr(application, "company_id", None):
        from app.services.enterprise import credit_service as _cs

        await _cs.record_usage(
            application.company_id,
            "assessment",
            "attempt",
            reference_type="assessment_attempt",
            reference_id=str(attempt.id),
            description="Assessment attempt completed",
            session=session,
        )

    await session.commit()
    if is_video:
        return {"status": "success", "score": None, "pending_review": True}
    return {"status": "success", "score": overall_score}


@router.post("/{attempt_id}/video/{question_id}")
async def upload_video_answer(
    attempt_id: uuid.UUID, question_id: str, session: DBSessionDep, video: UploadFile = File(...)
) -> dict[str, Any]:
    """Store one recorded video answer for a question, keyed by question id in ``answers``.

    The candidate uploads a clip per question during a VIDEO assessment, then calls submit to
    finalise. Files are saved under uploads/assessment_videos and served from /uploads/…
    """
    attempt = (
        await session.execute(select(AssessmentAttempt).where(AssessmentAttempt.id == attempt_id))
    ).scalar_one_or_none()
    if not attempt or attempt.status == "COMPLETED":
        raise HTTPException(status_code=404, detail="Attempt not found or already completed.")

    dest_dir = "uploads/assessment_videos"
    os.makedirs(dest_dir, exist_ok=True)
    ext = os.path.splitext(video.filename or "")[1].lower() or ".webm"
    if ext not in (".webm", ".mp4", ".mov", ".ogg", ".m4v"):
        raise HTTPException(status_code=422, detail="Unsupported video format.")
    fname = f"{uuid.uuid4().hex}{ext}"
    async with aiofiles.open(os.path.join(dest_dir, fname), "wb") as f:
        await f.write(await video.read())
    url = f"/uploads/assessment_videos/{fname}"

    # Reassign a fresh dict so SQLAlchemy detects the JSONB change.
    answers = dict(attempt.answers or {})
    answers[str(question_id)] = url
    attempt.answers = cast("Any", answers)
    await session.commit()
    return {"status": "success", "url": url}
