from typing import List, Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.core.database import db_manager
from app.core.dependencies import DBSessionDep, get_current_agent
from app.models.enterprise.onboarding import OnboardingTemplate
from app.models.enterprise.hiring_agent import HiringAgent
from app.schemas.enterprise.onboarding import OnboardingTemplateCreate, OnboardingTemplateResponse

router = APIRouter(prefix="/onboarding/templates", tags=["Onboarding Templates"])

@router.get("/", response_model=List[OnboardingTemplateResponse])
async def list_templates(
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    """List all onboarding templates."""
    stmt = select(OnboardingTemplate).order_by(OnboardingTemplate.name)
    result = await session.execute(stmt)
    return result.scalars().all()

@router.post("/", response_model=OnboardingTemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_template(
    template_in: OnboardingTemplateCreate,
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    """Create a new onboarding template."""
    # Check for duplicate name
    check_stmt = select(OnboardingTemplate).where(OnboardingTemplate.name == template_in.name)
    existing = await session.execute(check_stmt)
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="A template with this name already exists")
    
    template = OnboardingTemplate(**template_in.model_dump())
    session.add(template)
    await session.commit()
    await session.refresh(template)
    return template

@router.get("/{id}", response_model=OnboardingTemplateResponse)
async def get_template_details(
    id: UUID,
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    """Get onboarding template details."""
    stmt = select(OnboardingTemplate).where(OnboardingTemplate.id == id)
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
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    """Update an onboarding template."""
    stmt = select(OnboardingTemplate).where(OnboardingTemplate.id == id)
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
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    """Delete an onboarding template."""
    stmt = select(OnboardingTemplate).where(OnboardingTemplate.id == id)
    result = await session.execute(stmt)
    template = result.scalar_one_or_none()
    
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
        
    await session.delete(template)
    await session.commit()
    return None
