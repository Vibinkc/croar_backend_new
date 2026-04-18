from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.assessment import AssessmentAutomation, AssessmentType
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
    count: int = 10,
) -> list[dict[str, Any]]:
    """
    Generates preview questions without saving to DB.
    """
    return await generate_assessment_questions(type, topic, count)


@router.post("/", response_model=AssessmentAutomationResponse)
async def create_assessment_automation(
    automation_in: AssessmentAutomationCreate,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.assessments, PermissionAction.create))
    ],
) -> AssessmentAutomation:
    company_id = getattr(current_user, "company_id", None)
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
) -> AssessmentAutomation:
    company_id = getattr(current_user, "company_id", None)
    stmt = select(AssessmentAutomation).where(
        AssessmentAutomation.id == automation_id, AssessmentAutomation.company_id == company_id
    )
    result = await db.execute(stmt)
    db_auto = result.scalar_one_or_none()

    if not db_auto:
        raise HTTPException(status_code=404, detail="Automation not found")

    questions = await generate_assessment_questions(db_auto.type, db_auto.topic, db_auto.question_count)
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
