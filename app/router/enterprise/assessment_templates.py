from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload
from typing import List, Dict
from uuid import UUID

from app.core.dependencies import get_db, get_current_agent
from app.models.enterprise.assessment import AssessmentTemplate, AssessmentAttempt, AssessmentType
from app.models.enterprise.candidate import CandidateApplication
from app.services.enterprise.ai_service import generate_assessment_questions
from app.schemas.enterprise.assessment import (
    AssessmentTemplateCreate,
    AssessmentTemplateUpdate,
    AssessmentTemplateResponse,
    BulkSendAssessmentRequest
)
from app.models.enterprise.job import JobRequirement

router = APIRouter(prefix="/assessment-templates", tags=["Assessment Templates"])

@router.get("/", response_model=List[AssessmentTemplateResponse])
async def list_templates(
    db: AsyncSession = Depends(get_db),
    current_agent = Depends(get_current_agent)
):
    stmt = (
        select(AssessmentTemplate)
        .options(selectinload(AssessmentTemplate.email_template))
        .order_by(AssessmentTemplate.created_at.desc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()

@router.post("/", response_model=AssessmentTemplateResponse)
async def create_template(
    template_in: AssessmentTemplateCreate,
    db: AsyncSession = Depends(get_db),
    current_agent = Depends(get_current_agent)
):
    data = template_in.model_dump()
    db_template = AssessmentTemplate(**data)
    db.add(db_template)
    await db.commit()
    
    # Reload with relation
    stmt = (
        select(AssessmentTemplate)
        .where(AssessmentTemplate.id == db_template.id)
        .options(selectinload(AssessmentTemplate.email_template))
    )
    result = await db.execute(stmt)
    return result.scalar_one()

@router.get("/{template_id}", response_model=AssessmentTemplateResponse)
async def get_template(
    template_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_agent = Depends(get_current_agent)
):
    stmt = (
        select(AssessmentTemplate)
        .where(AssessmentTemplate.id == template_id)
        .options(selectinload(AssessmentTemplate.email_template))
    )
    result = await db.execute(stmt)
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template

@router.patch("/{template_id}", response_model=AssessmentTemplateResponse)
async def update_template(
    template_id: UUID,
    template_in: AssessmentTemplateUpdate,
    db: AsyncSession = Depends(get_db),
    current_agent = Depends(get_current_agent)
):
    stmt = select(AssessmentTemplate).where(AssessmentTemplate.id == template_id)
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
async def delete_template(
    template_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_agent = Depends(get_current_agent)
):
    stmt = select(AssessmentTemplate).where(AssessmentTemplate.id == template_id)
    result = await db.execute(stmt)
    db_template = result.scalar_one_or_none()
    
    if not db_template:
        raise HTTPException(status_code=404, detail="Template not found")
        
    await db.delete(db_template)
    await db.commit()
    return None

@router.post("/bulk-send")
async def bulk_send_assessment(
    request: BulkSendAssessmentRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_agent = Depends(get_current_agent)
):
    """
    Manually triggers an assessment for multiple candidates using a template.
    """
    # 1. Verify template exists
    stmt = select(AssessmentTemplate).where(AssessmentTemplate.id == request.template_id)
    result = await db.execute(stmt)
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # 2. Get applications and their jobs/candidates
    stmt = (
        select(CandidateApplication)
        .where(CandidateApplication.id.in_(request.application_ids))
        .options(selectinload(CandidateApplication.candidate), selectinload(CandidateApplication.job_requirement))
    )
    result = await db.execute(stmt)
    applications = result.scalars().all()

    # 3. Create attempts and send emails
    from app.services.enterprise.automation_service import send_manual_assessment_invitation
    attempts_created = 0
    for app in applications:
        # Create attempt
        new_attempt = AssessmentAttempt(
            template_id=template.id,
            candidate_id=app.candidate_id,
            application_id=app.id,
            status="STARTED"
        )
        db.add(new_attempt)
        
        # Trigger email invitation
        if app.candidate and app.job_requirement:
            await send_manual_assessment_invitation(
                template=template,
                application=app,
                candidate=app.candidate,
                job=app.job_requirement,
                session=db,
                background_tasks=background_tasks
            )
        
        attempts_created += 1

    await db.commit()
    return {"message": f"Successfully triggered assessment for {attempts_created} candidates."}
