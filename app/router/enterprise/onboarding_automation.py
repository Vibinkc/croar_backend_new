import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.onboarding import OnboardingAutomation
from app.models.shared.constants import ModuleScope, PermissionAction
from app.schemas.enterprise.onboarding import (
    OnboardingAutomationCreate,
    OnboardingAutomationResponse,
    OnboardingAutomationUpdate,
)

router = APIRouter(prefix="/onboarding-automation", tags=["Enterprise: Onboarding Automation"])


@router.get("/", response_model=list[OnboardingAutomationResponse])
async def list_onboarding_automations(
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.onboarding, PermissionAction.read))
    ],
    job_id: uuid.UUID | None = None,
) -> list[object]:
    query = (
        select(OnboardingAutomation)
        .options(
            selectinload(OnboardingAutomation.template), selectinload(OnboardingAutomation.email_template)
        )
        .where(OnboardingAutomation.company_id == getattr(current_user, "company_id", None))
    )
    if job_id:
        query = query.where(OnboardingAutomation.job_requirement_id == str(job_id))

    query = query.order_by(OnboardingAutomation.created_at.desc())
    result = await db.execute(query)
    return list(result.scalars().all())


@router.post("/", response_model=OnboardingAutomationResponse)
async def create_onboarding_automation(
    automation_in: OnboardingAutomationCreate,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.onboarding, PermissionAction.create))
    ],
) -> object:
    new_automation = OnboardingAutomation(
        **automation_in.model_dump(), company_id=getattr(current_user, "company_id", None)
    )
    db.add(new_automation)
    await db.commit()
    await db.refresh(new_automation)

    # Re-fetch with relationships
    stmt = (
        select(OnboardingAutomation)
        .options(
            selectinload(OnboardingAutomation.template), selectinload(OnboardingAutomation.email_template)
        )
        .where(OnboardingAutomation.id == new_automation.id)
    )
    result = await db.execute(stmt)
    return result.scalar_one()


@router.patch("/{automation_id}", response_model=OnboardingAutomationResponse)
async def update_onboarding_automation(
    automation_id: uuid.UUID,
    update_data: OnboardingAutomationUpdate,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.onboarding, PermissionAction.update))
    ],
) -> object:
    stmt = select(OnboardingAutomation).where(
        OnboardingAutomation.id == automation_id,
        OnboardingAutomation.company_id == getattr(current_user, "company_id", None),
    )
    result = await db.execute(stmt)
    automation = result.scalar_one_or_none()

    if not automation:
        raise HTTPException(status_code=404, detail="Onboarding automation not found")

    update_dict = update_data.model_dump(exclude_unset=True)
    for key, value in update_dict.items():
        setattr(automation, key, value)

    await db.commit()
    await db.refresh(automation)

    # Re-fetch with relationships
    stmt = (
        select(OnboardingAutomation)
        .options(
            selectinload(OnboardingAutomation.template), selectinload(OnboardingAutomation.email_template)
        )
        .where(OnboardingAutomation.id == automation_id)
    )
    result = await db.execute(stmt)
    return result.scalar_one()


@router.delete("/{automation_id}")
async def delete_onboarding_automation(
    automation_id: uuid.UUID,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.onboarding, PermissionAction.delete))
    ],
) -> dict[str, str]:
    stmt = select(OnboardingAutomation).where(
        OnboardingAutomation.id == automation_id,
        OnboardingAutomation.company_id == getattr(current_user, "company_id", None),
    )
    result = await db.execute(stmt)
    automation = result.scalar_one_or_none()

    if not automation:
        raise HTTPException(status_code=404, detail="Onboarding automation not found")

    await db.delete(automation)
    await db.commit()
    return {"message": "Onboarding automation deleted successfully"}
