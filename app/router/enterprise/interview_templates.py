import uuid
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.dependencies import get_db, get_current_agent
from app.models.enterprise.interview import Interview
from app.models.enterprise.user_role import EnterpriseUser
from app.schemas.enterprise.interviews import InterviewCreate, InterviewResponse
from app.services.enterprise.ai_service import generate_interview_questions_service

router = APIRouter(prefix="/interview-templates", tags=["Enterprise: Interview Templates"])

@router.get("/", response_model=List[InterviewResponse])
async def list_interview_templates(
    db: AsyncSession = Depends(get_db),
    current_user: EnterpriseUser = Depends(get_current_agent),
):
    query = select(Interview).where(Interview.deleted_at == None).order_by(Interview.created_at.desc())
    result = await db.execute(query)
    return result.scalars().all()

@router.post("/", response_model=InterviewResponse)
async def create_interview_template(
    template_in: InterviewCreate,
    db: AsyncSession = Depends(get_db),
    current_user: EnterpriseUser = Depends(get_current_agent),
):
    new_template = Interview(**template_in.model_dump())
    db.add(new_template)
    await db.commit()
    await db.refresh(new_template)
    return new_template

@router.post("/generate-questions")
async def generate_questions(
    topic: str,
    duration: int = 30,
    difficulty: str = "Intermediate",
    current_user: EnterpriseUser = Depends(get_current_agent),
):
    # Calculate count: 1 question every 3 minutes, min 5 questions
    count = max(5, duration // 3)
    questions = await generate_interview_questions_service(topic, count, difficulty)
    return {"questions": questions}

@router.get("/{template_id}", response_model=InterviewResponse)
async def get_interview_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: EnterpriseUser = Depends(get_current_agent),
):
    stmt = select(Interview).where(Interview.id == str(template_id))
    result = await db.execute(stmt)
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template

@router.patch("/{template_id}", response_model=InterviewResponse)
async def update_interview_template(
    template_id: uuid.UUID,
    template_in: InterviewCreate,
    db: AsyncSession = Depends(get_db),
    current_user: EnterpriseUser = Depends(get_current_agent),
):
    stmt = select(Interview).where(Interview.id == str(template_id))
    result = await db.execute(stmt)
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    
    update_data = template_in.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(template, key, value)
    
    await db.commit()
    await db.refresh(template)
    return template

@router.delete("/{template_id}")
async def delete_interview_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: EnterpriseUser = Depends(get_current_agent),
):
    stmt = select(Interview).where(Interview.id == str(template_id))
    result = await db.execute(stmt)
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    
    await db.delete(template)
    await db.commit()
    return {"message": "Template deleted successfully"}
