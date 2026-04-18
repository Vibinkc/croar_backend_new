import uuid
from typing import Annotated, Any, cast

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
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.interviews, PermissionAction.read))
    ],
    job_id: uuid.UUID | None = None,
) -> list[InterviewAutomation]:
    company_id = getattr(current_user, "company_id", None)
    query = (
        select(InterviewAutomation)
        .where(InterviewAutomation.company_id == company_id)
        .options(selectinload(InterviewAutomation.email_template))
    )
    if job_id:
        query = query.where(InterviewAutomation.job_requirement_id == job_id)

    query = query.order_by(InterviewAutomation.created_at.desc())
    result = await db.execute(query)
    return list(result.scalars().all())


@router.post("/", response_model=InterviewAutomationResponse)
async def create_interview_automation(
    automation_in: InterviewAutomationCreate,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.interviews, PermissionAction.create))
    ],
) -> InterviewAutomation:
    company_id = getattr(current_user, "company_id", None)
    new_automation = InterviewAutomation(**automation_in.model_dump(), company_id=cast("Any", company_id))
    db.add(new_automation)
    await db.commit()
    await db.refresh(new_automation)

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
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.interviews, PermissionAction.update))
    ],
) -> InterviewAutomation:
    company_id = getattr(current_user, "company_id", None)
    stmt = select(InterviewAutomation).where(
        # automation_id is UUID here
        InterviewAutomation.id == automation_id,
        InterviewAutomation.company_id == company_id,
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

    stmt_res = (
        select(InterviewAutomation)
        .options(selectinload(InterviewAutomation.email_template))
        .where(InterviewAutomation.id == automation_id)
    )
    result_res = await db.execute(stmt_res)
    return result_res.scalar_one()


@router.delete("/{automation_id}")
async def delete_interview_automation(
    automation_id: Annotated[uuid.UUID, Path(...)],
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.interviews, PermissionAction.delete))
    ],
) -> dict[str, str]:
    company_id = getattr(current_user, "company_id", None)
    stmt = select(InterviewAutomation).where(
        InterviewAutomation.id == automation_id, InterviewAutomation.company_id == company_id
    )
    result = await db.execute(stmt)
    automation = result.scalar_one_or_none()

    if not automation:
        raise HTTPException(status_code=404, detail="Interview automation not found")

    await db.delete(automation)
    await db.commit()
    return {"message": "Interview automation deleted successfully"}
