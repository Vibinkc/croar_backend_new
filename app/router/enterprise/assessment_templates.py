from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
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
