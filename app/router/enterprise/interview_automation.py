import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.dependencies import get_db, get_current_agent
from app.models.enterprise.interview import InterviewAutomation
from app.models.enterprise.user_role import EnterpriseUser
from app.schemas.enterprise.interviews import (
    InterviewAutomationCreate,
    InterviewAutomationUpdate,
    InterviewAutomationResponse,
)

router = APIRouter(prefix="/interview-automation", tags=["Enterprise: Interview Automation"])

@router.get("/", response_model=List[InterviewAutomationResponse])
async def list_interview_automations(
    job_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_db),
    current_user: EnterpriseUser = Depends(get_current_agent),
):
    query = select(InterviewAutomation).options(selectinload(InterviewAutomation.email_template))
    if job_id:
        query = query.where(InterviewAutomation.job_requirement_id == str(job_id))
    
    query = query.order_by(InterviewAutomation.created_at.desc())
    result = await db.execute(query)
    automations = result.scalars().all()
    return automations

@router.post("/", response_model=InterviewAutomationResponse)
async def create_interview_automation(
    automation_in: InterviewAutomationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: EnterpriseUser = Depends(get_current_agent),
):
    new_automation = InterviewAutomation(**automation_in.model_dump())
    db.add(new_automation)
    await db.commit()
    await db.refresh(new_automation)

    # Re-fetch with relationships
    stmt = select(InterviewAutomation).options(selectinload(InterviewAutomation.email_template)).where(InterviewAutomation.id == new_automation.id)
    result = await db.execute(stmt)
    return result.scalar_one()

@router.patch("/{automation_id}", response_model=InterviewAutomationResponse)
async def update_interview_automation(
    automation_id: uuid.UUID,
    update_data: InterviewAutomationUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: EnterpriseUser = Depends(get_current_agent),
):
    stmt = select(InterviewAutomation).where(InterviewAutomation.id == str(automation_id))
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
    stmt = select(InterviewAutomation).options(selectinload(InterviewAutomation.email_template)).where(InterviewAutomation.id == str(automation_id))
    result = await db.execute(stmt)
    return result.scalar_one()


@router.delete("/{automation_id}")
async def delete_interview_automation(
    automation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: EnterpriseUser = Depends(get_current_agent),
):
    stmt = select(InterviewAutomation).where(InterviewAutomation.id == str(automation_id))
    result = await db.execute(stmt)
    automation = result.scalar_one_or_none()
    
    if not automation:
        raise HTTPException(status_code=404, detail="Interview automation not found")
        
    await db.delete(automation)
    await db.commit()
    return {"message": "Interview automation deleted successfully"}
