import random
import string
from typing import List, Annotated, Optional
from uuid import UUID
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, status
from fastapi.concurrency import run_in_threadpool # Added for email sending
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep, get_current_agent
from app.models.enterprise.user_role import EnterpriseUser as HiringAgent
from app.models.enterprise.onboarding import Onboarding, OnboardingStatus, OnboardingDocument, OnboardingActivity, OnboardingTask, OnboardingNote, OnboardingTemplate
from app.models.enterprise.candidate import CandidateApplication, Candidate
from app.models.enterprise.job import JobRequirement # Added
from app.schemas.enterprise.onboarding import (
    OnboardingResponse, OnboardingInitiateRequest, OnboardingUpdateRequest,
    OnboardingNoteCreate, OnboardingTaskCreate, OnboardingDocumentRequest,
    OnboardingStatusResponse, OnboardingResubmitRequest, OnboardingApproveRequest,
    OnboardingNoteResponse, OnboardingTaskResponse, OnboardingDocumentResponse,
    OnboardingActivityResponse
)
from app.router.enterprise.communication import send_smtp_email # Added
from app.core.settings import get_settings # Added

_settings = get_settings() # Added

router = APIRouter(prefix="/onboarding", tags=["Enterprise Onboarding"])

def generate_onboarding_code() -> str:
    """Generate a unique onboarding code like ONB-XXXXX."""
    suffix = ''.join(random.choices(string.digits, k=5))
    return f"ONB-{suffix}"

async def log_activity(session: DBSessionDep, onboarding_id: UUID, action: str, performed_by: str, metadata: Optional[dict] = None):
    activity = OnboardingActivity(
        onboarding_id=onboarding_id,
        action=action,
        performed_by=performed_by,
        metadata_info=metadata
    )
    session.add(activity)
    await session.flush()

@router.get("/", response_model=List[OnboardingResponse])
async def list_onboardings(
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)],
    job_id: Optional[UUID] = None,
    candidate_id: Optional[UUID] = None
):
    """List all onboarding processes."""
    stmt = select(Onboarding).options(
        selectinload(Onboarding.status),
        selectinload(Onboarding.template),
        selectinload(Onboarding.documents),
        selectinload(Onboarding.activities),
        selectinload(Onboarding.tasks),
        selectinload(Onboarding.notes),
        selectinload(Onboarding.application).selectinload(CandidateApplication.candidate),
        selectinload(Onboarding.application).selectinload(CandidateApplication.job_requirement)
    )
    
    if job_id:
        stmt = stmt.join(CandidateApplication, Onboarding.application_id == CandidateApplication.id).where(CandidateApplication.job_requirement_id == job_id)
        
    if candidate_id:
        stmt = stmt.join(CandidateApplication, Onboarding.application_id == CandidateApplication.id).where(CandidateApplication.candidate_id == candidate_id)
        
    stmt = stmt.order_by(Onboarding.created_at.desc())

    result = await session.execute(stmt)
    return result.scalars().all()

@router.post("/initiate", response_model=OnboardingResponse, status_code=201)
async def initiate_onboarding(
    request: OnboardingInitiateRequest,
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)],
    background_tasks: BackgroundTasks
):
    """Initiate onboarding for a candidate."""
    # 1. Verify application exists and isn't already onboarding
    stmt = select(CandidateApplication).options(selectinload(CandidateApplication.candidate)).where(CandidateApplication.id == request.application_id)
    result = await session.execute(stmt)
    application = result.scalar_one_or_none()

    if not application:
        raise HTTPException(status_code=404, detail="Candidate application not found")

    check_stmt = select(Onboarding).where(Onboarding.application_id == request.application_id)
    res_check = await session.execute(check_stmt)
    if res_check.scalar_one_or_none():
         raise HTTPException(status_code=400, detail="Onboarding already initiated for this candidate")

    from app.services.enterprise.onboarding_service import initiate_onboarding_process
    
    onboarding = await initiate_onboarding_process(
        session=session,
        application_id=request.application_id,
        template_id=request.template_id,
        performed_by=f"{current_agent.first_name} {current_agent.last_name or ''}".strip(),
        background_tasks=background_tasks
    )

    if not onboarding:
        raise HTTPException(status_code=400, detail="Onboarding already initiated for this candidate")

    
    # Reload with all relationships for OnboardingResponse
    stmt = select(Onboarding).options(
        selectinload(Onboarding.status),
        selectinload(Onboarding.template),
        selectinload(Onboarding.documents),
        selectinload(Onboarding.activities),
        selectinload(Onboarding.tasks),
        selectinload(Onboarding.notes),
        selectinload(Onboarding.application).selectinload(CandidateApplication.candidate),
        selectinload(Onboarding.application).selectinload(CandidateApplication.job_requirement)
    ).where(Onboarding.id == onboarding.id)
    
    result = await session.execute(stmt)
    onboarding_complete = result.scalar_one_or_none()

    return onboarding_complete

@router.get("/{id}", response_model=OnboardingResponse)
async def get_onboarding(
    id: UUID,
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    """Get detailed onboarding info."""
    stmt = select(Onboarding).options(
        selectinload(Onboarding.status),
        selectinload(Onboarding.template),
        selectinload(Onboarding.documents),
        selectinload(Onboarding.activities),
        selectinload(Onboarding.tasks),
        selectinload(Onboarding.notes),
        selectinload(Onboarding.application).selectinload(CandidateApplication.candidate),
        selectinload(Onboarding.application).selectinload(CandidateApplication.job_requirement)
    ).where(Onboarding.id == id)

    result = await session.execute(stmt)
    onboarding = result.scalar_one_or_none()

    if not onboarding:
        raise HTTPException(status_code=404, detail="Onboarding process not found")

    return onboarding

@router.get("/statuses", response_model=List[OnboardingStatusResponse])
async def get_onboarding_statuses(
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    """List all available onboarding statuses."""
    stmt = select(OnboardingStatus)
    result = await session.execute(stmt)
    return result.scalars().all()

@router.post("/{id}/resubmit", response_model=OnboardingResponse)
async def resubmit_onboarding(
    id: UUID,
    request: OnboardingResubmitRequest,
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)],
    background_tasks: BackgroundTasks
):
    """Request corrections for onboarding (selective rejection)."""
    stmt = select(Onboarding).options(
        selectinload(Onboarding.application).selectinload(CandidateApplication.candidate),
        selectinload(Onboarding.application).selectinload(CandidateApplication.job_requirement)
    ).where(Onboarding.id == id)
    result = await session.execute(stmt)
    onboarding = result.scalar_one_or_none()

    if not onboarding:
        raise HTTPException(status_code=404, detail="Onboarding not found")

    # 1. Update status to 'Action Required' or 'Rejected'
    status_stmt = select(OnboardingStatus).where(OnboardingStatus.name.in_(["Action Required", "Rejected"]))
    res_status = await session.execute(status_stmt)
    new_status = res_status.scalar_one_or_none()
    if new_status:
        onboarding.status_id = new_status.id

    # Update document statuses
    if request.rejected_document_ids:
        for doc_id in request.rejected_document_ids:
            doc_stmt = select(OnboardingDocument).where(OnboardingDocument.id == doc_id, OnboardingDocument.onboarding_id == onboarding.id)
            doc_res = await session.execute(doc_stmt)
            doc = doc_res.scalar_one_or_none()
            if doc:
                doc.status = "Rejected"
                doc.comment = request.reason

    # Save rejected fields
    onboarding.rejected_fields = request.rejected_fields or []

    # Log activity
    agent_name = f"{current_agent.first_name} {current_agent.last_name or ''}".strip()
    desc = f"Correction requested: {request.reason}"
    if request.rejected_document_ids:
        desc += f" ({len(request.rejected_document_ids)} documents rejected)"
    if request.rejected_fields:
        desc += f" ({len(request.rejected_fields)} fields rejected)"
    
    await log_activity(session, onboarding.id, "Correction Requested", agent_name, {"reason": request.reason, "rejected_documents": request.rejected_document_ids, "rejected_fields": request.rejected_fields})

    # 4. Notify Candidate
    from app.services.enterprise.onboarding_service import send_onboarding_resubmit_email
    await send_onboarding_resubmit_email(
        session=session,
        onboarding=onboarding,
        reason=request.reason,
        background_tasks=background_tasks
    )

    await session.commit()
    return await get_onboarding(id, session, current_agent)

@router.post("/{id}/approve", response_model=OnboardingResponse)
async def approve_onboarding(
    id: UUID,
    request: OnboardingApproveRequest,
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)],
    background_tasks: BackgroundTasks
):
    """Finalize/Approve onboarding and move candidate to Hired."""
    stmt = select(Onboarding).options(
        selectinload(Onboarding.application).selectinload(CandidateApplication.candidate),
        selectinload(Onboarding.application).selectinload(CandidateApplication.job_requirement)
    ).where(Onboarding.id == id)
    result = await session.execute(stmt)
    onboarding = result.scalar_one_or_none()

    if not onboarding:
        raise HTTPException(status_code=404, detail="Onboarding not found")

    # 1. Update Onboarding status to 'Completed'
    status_stmt = select(OnboardingStatus).where(OnboardingStatus.name == "Completed")
    res_status = await session.execute(status_stmt)
    new_status = res_status.scalar_one_or_none()
    if new_status:
        onboarding.status_id = new_status.id
        onboarding.completed_at = datetime.now()

    # 2. Update Application stage to 'Offer / Hired' (Stage 5)
    if onboarding.application:
        onboarding.application.current_stage = 5 # Standard Hired stage index
        
        # Trigger any automations for the Hired stage
        from app.services.enterprise.automation_service import trigger_automations
        await trigger_automations(onboarding.application.id, 5, session, background_tasks)

    # 3. Log Activity
    agent_name = f"{current_agent.first_name} {current_agent.last_name or ''}".strip()
    await log_activity(session, onboarding.id, "Onboarding Approved & Finalized", agent_name)

    # 4. Notify Candidate (Special Welcome email)
    from app.services.enterprise.onboarding_service import send_onboarding_welcome_email
    await send_onboarding_welcome_email(
        session=session,
        onboarding=onboarding,
        background_tasks=background_tasks
    )

    await session.commit()
    return await get_onboarding(id, session, current_agent)

@router.patch("/{id}", response_model=OnboardingResponse)
async def update_onboarding(
    id: UUID,
    request: OnboardingUpdateRequest,
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    """Update onboarding details or status."""
    stmt = select(Onboarding).where(Onboarding.id == id)
    result = await session.execute(stmt)
    onboarding = result.scalar_one_or_none()

    if not onboarding:
        raise HTTPException(status_code=404, detail="Onboarding process not found")

    update_data = request.model_dump(exclude_unset=True)

    # If status is changing, log it
    if "status_id" in update_data and update_data["status_id"] != onboarding.status_id:
        status_stmt = select(OnboardingStatus).where(OnboardingStatus.id == update_data["status_id"])
        res_status = await session.execute(status_stmt)
        new_status = res_status.scalar_one_or_none()
        if new_status:
            agent_name = f"{current_agent.first_name} {current_agent.last_name or ''}".strip()
            await log_activity(session, onboarding.id, f"Onboarding status changed to {new_status.name}", agent_name)

            if new_status.name == "Completed":
                onboarding.completed_at = datetime.now()

    for key, value in update_data.items():
        setattr(onboarding, key, value)

    await session.commit()
    
    # Reload with all relationships for OnboardingResponse
    stmt = select(Onboarding).options(
        selectinload(Onboarding.status),
        selectinload(Onboarding.template),
        selectinload(Onboarding.documents),
        selectinload(Onboarding.activities),
        selectinload(Onboarding.tasks),
        selectinload(Onboarding.notes),
        selectinload(Onboarding.application).selectinload(CandidateApplication.candidate),
        selectinload(Onboarding.application).selectinload(CandidateApplication.job_requirement)
    ).where(Onboarding.id == id)
    
    result = await session.execute(stmt)
    onboarding_complete = result.scalar_one_or_none()

    return onboarding_complete

@router.post("/{id}/notes", response_model=OnboardingNoteResponse)
async def add_note(
    id: UUID,
    request: OnboardingNoteCreate,
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    """Add a note to onboarding."""
    onboarding = await session.get(Onboarding, id)
    if not onboarding:
        raise HTTPException(status_code=404, detail="Onboarding not found")
        
    agent_name = f"{current_agent.first_name} {current_agent.last_name or ''}".strip()
    note = OnboardingNote(
        onboarding_id=id,
        content=request.content,
        author_name=agent_name
    )
    session.add(note)
    await session.commit()
    await session.refresh(note)
    return note

@router.post("/{id}/tasks", response_model=OnboardingTaskResponse)
async def add_task(
    id: UUID,
    request: OnboardingTaskCreate,
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    """Add a task to onboarding."""
    onboarding = await session.get(Onboarding, id)
    if not onboarding:
        raise HTTPException(status_code=404, detail="Onboarding not found")
        
    task = OnboardingTask(
        onboarding_id=id,
        **request.model_dump()
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task

@router.post("/{id}/documents/request", response_model=OnboardingDocumentResponse)
async def request_document(
    id: UUID,
    request: OnboardingDocumentRequest,
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    """Request a document from candidate."""
    onboarding = await session.get(Onboarding, id)
    if not onboarding:
        raise HTTPException(status_code=404, detail="Onboarding not found")
        
    doc = OnboardingDocument(
        onboarding_id=id,
        name=request.name,
        due_date=request.due_date,
        status="Pending"
    )
    session.add(doc)
    
    agent_name = f"{current_agent.first_name} {current_agent.last_name or ''}".strip()
    await log_activity(session, onboarding.id, f"Document request sent: {request.name}", agent_name)
    
    await session.commit()
    await session.refresh(doc)
    return doc
