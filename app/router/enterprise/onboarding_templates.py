from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.onboarding import OnboardingTemplate
from app.models.shared.constants import ModuleScope, PermissionAction
from app.schemas.enterprise.onboarding import OnboardingTemplateCreate, OnboardingTemplateResponse

router = APIRouter(prefix="/onboarding/templates", tags=["Onboarding Templates"])


@router.get("/", response_model=list[OnboardingTemplateResponse])
async def list_templates(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.onboarding, PermissionAction.read))
    ],
) -> list[object]:
    """List all onboarding templates."""
    stmt = (
        select(OnboardingTemplate)
        .where(OnboardingTemplate.company_id == getattr(current_user, "company_id", None))
        .order_by(OnboardingTemplate.name)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.post("/", response_model=OnboardingTemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_template(
    template_in: OnboardingTemplateCreate,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.onboarding, PermissionAction.create))
    ],
) -> object:
    """Create a new onboarding template."""
    # Check for duplicate name WITHIN the company
    check_stmt = select(OnboardingTemplate).where(
        OnboardingTemplate.name == template_in.name,
        OnboardingTemplate.company_id == getattr(current_user, "company_id", None),
    )
    existing = await session.execute(check_stmt)
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=400, detail="A template with this name already exists in your organization"
        )

    template = OnboardingTemplate(
        **template_in.model_dump(), company_id=getattr(current_user, "company_id", None)
    )
    session.add(template)
    await session.commit()
    await session.refresh(template)
    return template


@router.get("/{id}", response_model=OnboardingTemplateResponse)
async def get_template_details(
    id: UUID,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.onboarding, PermissionAction.read))
    ],
) -> object:
    """Get onboarding template details."""
    stmt = select(OnboardingTemplate).where(
        OnboardingTemplate.id == id,
        OnboardingTemplate.company_id == getattr(current_user, "company_id", None),
    )
    result = await session.execute(stmt)
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.put("/{id}", response_model=OnboardingTemplateResponse)
async def update_template(
    id: UUID,
    template_in: OnboardingTemplateCreate,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.onboarding, PermissionAction.update))
    ],
) -> object:
    """Update an onboarding template."""
    stmt = select(OnboardingTemplate).where(
        OnboardingTemplate.id == id,
        OnboardingTemplate.company_id == getattr(current_user, "company_id", None),
    )
    result = await session.execute(stmt)
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    for field, value in template_in.model_dump().items():
        setattr(template, field, value)

    await session.commit()
    await session.refresh(template)
    return template


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    id: UUID,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.onboarding, PermissionAction.delete))
    ],
) -> None:
    """Delete an onboarding template."""
    stmt = select(OnboardingTemplate).where(
        OnboardingTemplate.id == id,
        OnboardingTemplate.company_id == getattr(current_user, "company_id", None),
    )
    result = await session.execute(stmt)
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    await session.delete(template)
    await session.commit()
    return
