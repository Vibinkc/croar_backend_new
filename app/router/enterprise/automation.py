from datetime import timezone
from typing import List, Optional, Annotated
from uuid import UUID
from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.core.dependencies import DBSessionDep
from app.models.enterprise.communication import MailAutomation
from app.models.enterprise.job import JobRequirement
from app.schemas.enterprise.communication import (
    MailAutomationCreate,
    MailAutomationUpdate,
    MailAutomationResponse,
)

router = APIRouter(prefix="/automation", tags=["Mail Automation"])


@router.get("/mail", response_model=List[MailAutomationResponse])
async def list_automations(
    session: DBSessionDep,
    job_id: Optional[UUID] = None,
):
    """List all mail automations, optionally filtered by job."""
    stmt = select(MailAutomation)
    if job_id:
        stmt = stmt.where(MailAutomation.job_requirement_id == job_id)
    stmt = stmt.order_by(MailAutomation.created_at.desc())
    result = await session.execute(stmt)
    return result.scalars().all()


@router.post("/mail", response_model=MailAutomationResponse, status_code=201)
async def create_automation(
    request: MailAutomationCreate,
    session: DBSessionDep,
):
    """Create a new mail automation rule."""
    # Validate job exists
    job_stmt = select(JobRequirement).where(
        JobRequirement.id == request.job_requirement_id,
    )
    job_result = await session.execute(job_stmt)
    if not job_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Job not found")

    data = request.model_dump()
    if data.get("send_at") and data["send_at"].tzinfo:
        data["send_at"] = data["send_at"].astimezone(timezone.utc).replace(tzinfo=None)

    automation = MailAutomation(
        **data,
    )
    session.add(automation)
    await session.commit()
    await session.refresh(automation)
    return automation


@router.patch("/mail/{automation_id}", response_model=MailAutomationResponse)
async def update_automation(
    automation_id: UUID,
    request: MailAutomationUpdate,
    session: DBSessionDep,
):
    """Update an automation rule (including toggling is_enabled)."""
    stmt = select(MailAutomation).where(
        MailAutomation.id == automation_id,
    )
    result = await session.execute(stmt)
    automation = result.scalar_one_or_none()
    if not automation:
        raise HTTPException(status_code=404, detail="Automation not found")

    update_data = request.model_dump(exclude_unset=True)
    if update_data.get("send_at") and update_data["send_at"].tzinfo:
        update_data["send_at"] = update_data["send_at"].astimezone(timezone.utc).replace(tzinfo=None)

    for key, value in update_data.items():
        setattr(automation, key, value)

    await session.commit()
    await session.refresh(automation)
    return automation


@router.delete("/mail/{automation_id}")
async def delete_automation(
    automation_id: UUID,
    session: DBSessionDep,
):
    """Delete a mail automation rule."""
    stmt = select(MailAutomation).where(
        MailAutomation.id == automation_id,
    )
    result = await session.execute(stmt)
    automation = result.scalar_one_or_none()
    if not automation:
        raise HTTPException(status_code=404, detail="Automation not found")

    await session.delete(automation)
    await session.commit()
    return {"message": "Automation deleted successfully"}
