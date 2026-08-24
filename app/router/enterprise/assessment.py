from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.assessment import (
    AssessmentAttempt,
    AssessmentAutomation,
    AssessmentTemplate,
    AssessmentType,
)
from app.models.enterprise.candidate import Candidate, CandidateApplication
from app.models.shared.constants import ModuleScope, PermissionAction
from app.schemas.enterprise.assessment import (
    AssessmentAutomationCreate,
    AssessmentAutomationResponse,
    AssessmentAutomationUpdate,
)
from app.services.enterprise.ai_service import generate_assessment_questions

router = APIRouter(prefix="/assessment", tags=["Assessment Automation"])


@router.get("/", response_model=list[AssessmentAutomationResponse])
async def list_assessment_automations(
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.assessments, PermissionAction.read))
    ],
) -> list[AssessmentAutomation]:
    company_id = getattr(current_user, "company_id", None)
    stmt = (
        select(AssessmentAutomation)
        .where(AssessmentAutomation.company_id == company_id)
        .options(selectinload(AssessmentAutomation.email_template))
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("/generate-preview", response_model=list[dict[str, Any]])
async def generate_preview_questions(
    _current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.assessments, PermissionAction.generate))
    ],
    type: AssessmentType,
    topic: str,
    count: int = Query(10, ge=1, le=50),
    language: str = "English",
) -> list[dict[str, Any]]:
    """
    Generates preview questions without saving to DB.
    """
    return await generate_assessment_questions(type, topic, count, language=language)


@router.post("/", response_model=AssessmentAutomationResponse)
async def create_assessment_automation(
    automation_in: AssessmentAutomationCreate,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.assessments, PermissionAction.create))
    ],
) -> AssessmentAutomation:
    from app.models.enterprise.job import JobRequirement

    company_id = getattr(current_user, "company_id", None)
    # Verify the target job belongs to the caller's company (parity with mail automation) so an
    # automation can't be created against another tenant's job id.
    job = (
        await db.execute(
            select(JobRequirement).where(
                JobRequirement.id == automation_in.job_requirement_id, JobRequirement.company_id == company_id
            )
        )
    ).scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    data = automation_in.model_dump()
    if data.get("send_at") and data["send_at"].tzinfo:
        data["send_at"] = data["send_at"].replace(tzinfo=None)
    db_auto = AssessmentAutomation(**data, company_id=cast("UUID", company_id))
    db.add(db_auto)
    await db.commit()

    # Refresh with relation loaded
    stmt = (
        select(AssessmentAutomation)
        .where(AssessmentAutomation.id == db_auto.id, AssessmentAutomation.company_id == company_id)
        .options(selectinload(AssessmentAutomation.email_template))
    )
    result = await db.execute(stmt)
    return result.scalar_one()


@router.post("/{automation_id}/generate", response_model=AssessmentAutomationResponse)
async def generate_questions(
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.assessments, PermissionAction.generate))
    ],
    automation_id: UUID,
    db: DBSessionDep,
    _count: int = 10,
    language: str = "English",
) -> AssessmentAutomation:
    company_id = getattr(current_user, "company_id", None)
    stmt = select(AssessmentAutomation).where(
        AssessmentAutomation.id == automation_id, AssessmentAutomation.company_id == company_id
    )
    result = await db.execute(stmt)
    db_auto = result.scalar_one_or_none()

    if not db_auto:
        raise HTTPException(status_code=404, detail="Automation not found")

    questions = await generate_assessment_questions(
        db_auto.type, db_auto.topic, db_auto.question_count, language=language
    )
    db_auto.generated_questions = questions

    await db.commit()

    # Reload with relation
    stmt = (
        select(AssessmentAutomation)
        .where(AssessmentAutomation.id == automation_id, AssessmentAutomation.company_id == company_id)
        .options(selectinload(AssessmentAutomation.email_template))
    )
    result = await db.execute(stmt)
    return result.scalar_one()


@router.patch("/{automation_id}", response_model=AssessmentAutomationResponse)
async def update_assessment_automation(
    automation_id: UUID,
    automation_in: AssessmentAutomationUpdate,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.assessments, PermissionAction.update))
    ],
) -> AssessmentAutomation:
    company_id = getattr(current_user, "company_id", None)
    stmt = select(AssessmentAutomation).where(
        AssessmentAutomation.id == automation_id, AssessmentAutomation.company_id == company_id
    )
    result = await db.execute(stmt)
    db_auto = result.scalar_one_or_none()

    if not db_auto:
        raise HTTPException(status_code=404, detail="Automation not found")

    update_data = automation_in.model_dump(exclude_unset=True)
    if update_data.get("send_at") and update_data["send_at"].tzinfo:
        update_data["send_at"] = update_data["send_at"].replace(tzinfo=None)
    for key, value in update_data.items():
        setattr(db_auto, key, value)

    await db.commit()

    # Reload with relation
    stmt = (
        select(AssessmentAutomation)
        .where(AssessmentAutomation.id == automation_id, AssessmentAutomation.company_id == company_id)
        .options(selectinload(AssessmentAutomation.email_template))
    )
    result = await db.execute(stmt)
    return result.scalar_one()


@router.delete("/{automation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_assessment_automation(
    automation_id: UUID,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.assessments, PermissionAction.delete))
    ],
) -> None:
    company_id = getattr(current_user, "company_id", None)
    stmt = select(AssessmentAutomation).where(
        AssessmentAutomation.id == automation_id, AssessmentAutomation.company_id == company_id
    )
    result = await db.execute(stmt)
    db_auto = result.scalar_one_or_none()

    if not db_auto:
        raise HTTPException(status_code=404, detail="Automation not found")

    await db.delete(db_auto)
    await db.commit()
    return


# ---------------------------------------------------------------------------
# Video-assessment review (HR watches recorded answers and scores them)
# ---------------------------------------------------------------------------
def _questions_for(auto: AssessmentAutomation | None, tpl: AssessmentTemplate | None) -> list[dict[str, Any]]:
    src = (auto.generated_questions if auto else None) or (tpl.generated_questions if tpl else None) or []
    return cast("list[dict[str, Any]]", src)


@router.get("/attempts/pending-review")
async def list_pending_video_reviews(
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.assessments, PermissionAction.read))
    ],
) -> list[dict[str, Any]]:
    """Video-assessment attempts awaiting an HR score, with the candidate + recorded answers."""
    company_id = getattr(current_user, "company_id", None)
    rows = (
        (
            await db.execute(
                select(AssessmentAttempt).where(
                    AssessmentAttempt.company_id == company_id, AssessmentAttempt.status == "PENDING_REVIEW"
                )
            )
        )
        .scalars()
        .all()
    )
    out: list[dict[str, Any]] = []
    for a in rows:
        cand = (
            await db.execute(select(Candidate).where(Candidate.id == a.candidate_id))
        ).scalar_one_or_none()
        auto = (
            (
                await db.execute(
                    select(AssessmentAutomation).where(AssessmentAutomation.id == a.automation_id)
                )
            ).scalar_one_or_none()
            if a.automation_id
            else None
        )
        tpl = (
            (
                await db.execute(select(AssessmentTemplate).where(AssessmentTemplate.id == a.template_id))
            ).scalar_one_or_none()
            if a.template_id
            else None
        )
        questions = _questions_for(auto, tpl)
        answers = cast("dict[str, Any]", a.answers or {})
        out.append(
            {
                "attempt_id": str(a.id),
                "application_id": str(a.application_id),
                "candidate_name": (cand.full_name if cand else None),
                "candidate_email": (cand.email if cand else None),
                "topic": (auto.topic if auto else (tpl.topic if tpl else None)),
                "submitted_at": a.completed_at.isoformat() if a.completed_at else None,
                "answers": [
                    {"question": q.get("question"), "video_url": answers.get(str(q.get("id")))}
                    for q in questions
                ],
            }
        )
    return out


class VideoReviewRequest(BaseModel):
    score: int  # 0-100
    feedback: str | None = None


@router.post("/attempts/{attempt_id}/review")
async def review_video_attempt(
    attempt_id: UUID,
    request: VideoReviewRequest,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.assessments, PermissionAction.moderate))
    ],
) -> dict[str, Any]:
    """HR scores a video attempt; on a pass (>=60) the candidate advances like a graded test."""
    company_id = getattr(current_user, "company_id", None)
    attempt = (
        await db.execute(
            select(AssessmentAttempt).where(
                AssessmentAttempt.id == attempt_id, AssessmentAttempt.company_id == company_id
            )
        )
    ).scalar_one_or_none()
    if not attempt:
        raise HTTPException(status_code=404, detail="Attempt not found")

    score = max(0, min(100, int(request.score)))
    attempt.score = score
    attempt.status = "COMPLETED"

    application = (
        await db.execute(
            select(CandidateApplication).where(CandidateApplication.id == attempt.application_id)
        )
    ).scalar_one_or_none()
    if application:
        application.ai_match_score = cast("Any", (float(application.ai_match_score or 0) + float(score)) / 2)
        await db.flush()
        if score >= 60:
            from app.services.enterprise.automation_service import trigger_automations

            await trigger_automations(application.id, application.current_stage, db)

    await db.commit()
    return {"status": "success", "score": score}
