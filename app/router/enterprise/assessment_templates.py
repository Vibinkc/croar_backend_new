from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.assessment import AssessmentTemplate
from app.models.shared.constants import ModuleScope, PermissionAction
from app.schemas.enterprise.assessment import (
    AssessmentTemplateCreate,
    AssessmentTemplateResponse,
    AssessmentTemplateUpdate,
)
from app.services.enterprise.ai_service import generate_assessment_questions


class BulkSendRequest(BaseModel):
    application_ids: list[UUID]
    template_id: UUID


router = APIRouter(prefix="/assessment-templates", tags=["Assessment Templates"])


@router.get("/", response_model=list[AssessmentTemplateResponse])
async def list_assessment_templates(
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.assessments, PermissionAction.read))
    ],
) -> list[AssessmentTemplate]:
    company_id = getattr(current_user, "company_id", None)
    stmt = (
        select(AssessmentTemplate)
        .where(AssessmentTemplate.company_id == company_id)
        .options(selectinload(AssessmentTemplate.email_template))
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("/", response_model=AssessmentTemplateResponse)
async def create_assessment_template(
    template_in: AssessmentTemplateCreate,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.assessments, PermissionAction.create))
    ],
) -> AssessmentTemplate:
    company_id = getattr(current_user, "company_id", None)
    db_template = AssessmentTemplate(**template_in.model_dump(), company_id=cast("UUID", company_id))
    db.add(db_template)
    await db.commit()

    # Refresh with relation
    stmt = (
        select(AssessmentTemplate)
        .where(AssessmentTemplate.id == db_template.id)
        .options(selectinload(AssessmentTemplate.email_template))
    )
    result = await db.execute(stmt)
    return result.scalar_one()


@router.get("/{template_id}", response_model=AssessmentTemplateResponse)
async def get_assessment_template(
    template_id: UUID,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.assessments, PermissionAction.read))
    ],
) -> AssessmentTemplate:
    company_id = getattr(current_user, "company_id", None)
    stmt = (
        select(AssessmentTemplate)
        .where(AssessmentTemplate.id == template_id, AssessmentTemplate.company_id == company_id)
        .options(selectinload(AssessmentTemplate.email_template))
    )
    result = await db.execute(stmt)
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.patch("/{template_id}", response_model=AssessmentTemplateResponse)
async def update_assessment_template(
    template_id: UUID,
    template_in: AssessmentTemplateUpdate,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.assessments, PermissionAction.update))
    ],
) -> AssessmentTemplate:
    company_id = getattr(current_user, "company_id", None)
    stmt = select(AssessmentTemplate).where(
        AssessmentTemplate.id == template_id, AssessmentTemplate.company_id == company_id
    )
    result = await db.execute(stmt)
    db_template = result.scalar_one_or_none()

    if not db_template:
        raise HTTPException(status_code=404, detail="Template not found")

    update_data = template_in.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_template, key, value)

    await db.commit()

    # Reload with relation
    stmt = (
        select(AssessmentTemplate)
        .where(AssessmentTemplate.id == template_id)
        .options(selectinload(AssessmentTemplate.email_template))
    )
    result = await db.execute(stmt)
    return result.scalar_one()


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_assessment_template(
    template_id: UUID,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.assessments, PermissionAction.delete))
    ],
) -> None:
    company_id = getattr(current_user, "company_id", None)
    stmt = select(AssessmentTemplate).where(
        AssessmentTemplate.id == template_id, AssessmentTemplate.company_id == company_id
    )
    result = await db.execute(stmt)
    db_template = result.scalar_one_or_none()

    if not db_template:
        raise HTTPException(status_code=404, detail="Template not found")

    await db.delete(db_template)
    await db.commit()
    return


@router.post("/{template_id}/generate", response_model=AssessmentTemplateResponse)
async def generate_template_questions(
    template_id: UUID,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.assessments, PermissionAction.generate))
    ],
    count: int = 10,
) -> AssessmentTemplate:
    company_id = getattr(current_user, "company_id", None)
    stmt = select(AssessmentTemplate).where(
        AssessmentTemplate.id == template_id, AssessmentTemplate.company_id == company_id
    )
    result = await db.execute(stmt)
    db_template = result.scalar_one_or_none()

    if not db_template:
        raise HTTPException(status_code=404, detail="Template not found")

    questions = await generate_assessment_questions(db_template.type, db_template.topic, count)
    db_template.generated_questions = questions
    db_template.question_count = count

    await db.commit()

    # Reload with relation
    stmt = (
        select(AssessmentTemplate)
        .where(AssessmentTemplate.id == template_id)
        .options(selectinload(AssessmentTemplate.email_template))
    )
    result = await db.execute(stmt)
    return result.scalar_one()


@router.post("/bulk-send")
async def bulk_send_assessment(
    request: BulkSendRequest,
    db: DBSessionDep,
    background_tasks: BackgroundTasks,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.assessments, PermissionAction.moderate))
    ],
) -> dict[str, Any]:
    """Send an assessment (by template) to a set of candidates by application id."""
    from app.core.settings import get_settings
    from app.models.enterprise.candidate import Candidate, CandidateApplication
    from app.models.enterprise.communication import EmailLog
    from app.models.enterprise.company import Company
    from app.models.enterprise.job import JobRequirement
    from app.router.enterprise.communication import send_smtp_email
    from app.utils.template_render import build_candidate_variables, render_template

    settings = get_settings()
    company_id = getattr(current_user, "company_id", None)

    template = (
        await db.execute(
            select(AssessmentTemplate)
            .where(AssessmentTemplate.id == request.template_id, AssessmentTemplate.company_id == company_id)
            .options(selectinload(AssessmentTemplate.email_template))
        )
    ).scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Assessment template not found.")
    if not template.generated_questions:
        raise HTTPException(
            status_code=400,
            detail="This assessment template has no questions yet. Add or generate questions before sending.",
        )

    email_tpl = template.email_template
    company = (await db.execute(select(Company).where(Company.id == company_id))).scalar_one_or_none()
    # Candidate take page: /assessment/take/{id} (verifies by email).
    assessment_link = f"{settings.frontend_url}/assessment/take/{template.id}"

    sent = 0
    skipped: list[str] = []
    for app_id in request.application_ids:
        application = (
            await db.execute(
                select(CandidateApplication).where(
                    CandidateApplication.id == app_id, CandidateApplication.company_id == company_id
                )
            )
        ).scalar_one_or_none()
        if not application:
            skipped.append(str(app_id))
            continue

        candidate = (
            await db.execute(select(Candidate).where(Candidate.id == application.candidate_id))
        ).scalar_one_or_none()
        if not candidate or not candidate.email:
            skipped.append(str(app_id))
            continue

        job = (
            await db.execute(
                select(JobRequirement).where(JobRequirement.id == application.job_requirement_id)
            )
        ).scalar_one_or_none()

        variables: dict[str, object] = {
            **build_candidate_variables(candidate.full_name),
            "job_title": job.title if job else "the role",
            "company_name": company.name if company else "Our Company",
            "assessment_link": assessment_link,
            "test_duration": str(template.test_duration),
            "topic": template.topic,
        }

        if email_tpl:
            subject = render_template(email_tpl.subject, variables)
            body = render_template(email_tpl.body, variables)
        else:
            subject = f"Assessment invitation: {template.topic}"
            body = (
                f"Hi {variables.get('candidate_name', 'Candidate')},\n\n"
                f"You've been invited to complete an assessment on {template.topic} "
                f"(about {template.test_duration} minutes).\n\n"
                f"Start it here: {assessment_link}\n\n"
                f"Use the same email you applied with to begin.\n\n"
                f"Best regards,\n{variables.get('company_name', 'The hiring team')}"
            )

        recipient = candidate.email
        background_tasks.add_task(send_smtp_email, recipient, subject, body)

        db.add(
            EmailLog(
                candidate_id=candidate.id,
                application_id=application.id,
                template_id=email_tpl.id if email_tpl else None,
                sender_email=settings.mailer_sender_email,
                recipient_email=recipient,
                subject=subject,
                body=body,
                direction="OUTBOUND",
                status="sent",
            )
        )
        sent += 1

    await db.commit()
    return {"sent": sent, "skipped": skipped}
