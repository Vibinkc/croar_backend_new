import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.interview import InterviewAutomation
from app.models.shared.constants import ModuleScope, PermissionAction
from app.schemas.enterprise.interviews import (
    InterviewAutomationCreate,
    InterviewAutomationResponse,
    InterviewAutomationUpdate,
)

router = APIRouter(prefix="/interview-automation", tags=["Enterprise: Interview Automation"])


@router.get("/", response_model=list[InterviewAutomationResponse])
async def list_interview_automations(
    db: DBSessionDep,
    current_user: Annotated[Any, Depends(PermissionChecker(ModuleScope.interviews, PermissionAction.read))],
    job_id: uuid.UUID | None = None,
):
    query = (
        select(InterviewAutomation)
        .where(InterviewAutomation.company_id == current_user.company_id)
        .options(selectinload(InterviewAutomation.email_template))
    )
    if job_id:
        query = query.where(InterviewAutomation.job_requirement_id == str(job_id))

    query = query.order_by(InterviewAutomation.created_at.desc())
    result = await db.execute(query)
    automations = result.scalars().all()
    return automations


@router.post("/", response_model=InterviewAutomationResponse)
async def create_interview_automation(
    automation_in: InterviewAutomationCreate,
    db: DBSessionDep,
    current_user: Annotated[Any, Depends(PermissionChecker(ModuleScope.interviews, PermissionAction.create))],
):
    new_automation = InterviewAutomation(**automation_in.model_dump(), company_id=current_user.company_id)
    db.add(new_automation)
    await db.commit()
    await db.refresh(new_automation)

    # Re-fetch with relationships
    stmt = (
        select(InterviewAutomation)
        .options(selectinload(InterviewAutomation.email_template))
        .where(InterviewAutomation.id == new_automation.id)
    )
    result = await db.execute(stmt)
    return result.scalar_one()


@router.patch("/{automation_id}", response_model=InterviewAutomationResponse)
async def update_interview_automation(
    automation_id: Annotated[uuid.UUID, Path(...)],
    update_data: InterviewAutomationUpdate,
    db: DBSessionDep,
    current_user: Annotated[Any, Depends(PermissionChecker(ModuleScope.interviews, PermissionAction.update))],
):
    stmt = select(InterviewAutomation).where(
        InterviewAutomation.id == str(automation_id),
        InterviewAutomation.company_id == current_user.company_id,
    )
    result = await db.execute(stmt)
    automation = result.scalar_one_or_none()

    if not automation:
        raise HTTPException(status_code=404, detail="Interview automation not found")

    update_dict = update_data.model_dump(exclude_unset=True)
    for key, value in update_dict.items():
        setattr(automation, key, value)

    await db.commit()
    await db.refresh(automation)

    # Re-fetch with relationships
    stmt = (
        select(InterviewAutomation)
        .options(selectinload(InterviewAutomation.email_template))
        .where(InterviewAutomation.id == str(automation_id))
    )
    result = await db.execute(stmt)
    return result.scalar_one()


@router.delete("/{automation_id}")
async def delete_interview_automation(
    automation_id: Annotated[uuid.UUID, Path(...)],
    db: DBSessionDep,
    current_user: Annotated[Any, Depends(PermissionChecker(ModuleScope.interviews, PermissionAction.delete))],
):
    stmt = select(InterviewAutomation).where(InterviewAutomation.id == str(automation_id))
    result = await db.execute(stmt)
    automation = result.scalar_one_or_none()

    if not automation:
        raise HTTPException(status_code=404, detail="Interview automation not found")

    await db.delete(automation)
    await db.commit()
    return {"message": "Interview automation deleted successfully"}
