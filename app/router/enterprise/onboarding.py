import random
import string
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep, PermissionChecker, get_current_user
from app.core.settings import get_settings  # Added
from app.models.enterprise.candidate import CandidateApplication
from app.models.enterprise.onboarding import (
    Onboarding,
    OnboardingActivity,
    OnboardingDocument,
    OnboardingNote,
    OnboardingStatus,
    OnboardingTask,
)
from app.models.shared.constants import ModuleScope, PermissionAction
from app.schemas.enterprise.onboarding import (
    OnboardingApproveRequest,
    OnboardingDocumentRequest,
    OnboardingDocumentResponse,
    OnboardingInitiateRequest,
    OnboardingNoteCreate,
    OnboardingNoteResponse,
    OnboardingResponse,
    OnboardingResubmitRequest,
    OnboardingStatusResponse,
    OnboardingTaskCreate,
    OnboardingTaskResponse,
    OnboardingUpdateRequest,
)

_settings = get_settings()  # Added

router = APIRouter(prefix="/onboarding", tags=["Enterprise Onboarding"])


def generate_onboarding_code() -> str:
    """Generate a unique onboarding code like ONB-XXXXX."""
    suffix = "".join(random.choices(string.digits, k=5))
    return f"ONB-{suffix}"


async def log_activity(
    session: DBSessionDep,
    onboarding_id: UUID,
    company_id: UUID,
    action: str,
    performed_by: str,
    metadata: dict | None = None,
):
    activity = OnboardingActivity(
        onboarding_id=onboarding_id,
        company_id=company_id,
        action=action,
        performed_by=performed_by,
        metadata_info=metadata,
    )
    session.add(activity)
    await session.flush()


@router.get("/", response_model=list[OnboardingResponse])
async def list_onboardings(
    session: DBSessionDep,
    current_user: Annotated[Any, Depends(PermissionChecker(ModuleScope.onboarding, PermissionAction.read))],
    job_id: UUID | None = None,
    candidate_id: UUID | None = None,
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
        selectinload(Onboarding.application).selectinload(CandidateApplication.job_requirement),
    )

    if job_id:
        stmt = stmt.join(CandidateApplication, Onboarding.application_id == CandidateApplication.id).where(
            CandidateApplication.job_requirement_id == job_id
        )

    if candidate_id:
        stmt = stmt.join(CandidateApplication, Onboarding.application_id == CandidateApplication.id).where(
            CandidateApplication.candidate_id == candidate_id
        )

    stmt = stmt.where(Onboarding.company_id == current_user.company_id).order_by(Onboarding.created_at.desc())

    result = await session.execute(stmt)
    return result.scalars().all()


@router.post("/initiate", response_model=OnboardingResponse, status_code=201)
async def initiate_onboarding(
    request: OnboardingInitiateRequest,
    session: DBSessionDep,
    current_user: Annotated[Any, Depends(PermissionChecker(ModuleScope.onboarding, PermissionAction.create))],
    background_tasks: BackgroundTasks,
):
    """Initiate onboarding for a candidate."""
    # 1. Verify application exists and isn't already onboarding
    stmt = (
        select(CandidateApplication)
        .options(selectinload(CandidateApplication.candidate))
        .where(CandidateApplication.id == request.application_id)
    )
    result = await session.execute(stmt)
    application = result.scalar_one_or_none()

    if not application or application.company_id != current_user.company_id:
        raise HTTPException(status_code=404, detail="Candidate application not found")

    check_stmt = select(Onboarding).where(
        Onboarding.application_id == request.application_id, Onboarding.company_id == current_user.company_id
    )
    res_check = await session.execute(check_stmt)
    if res_check.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Onboarding already initiated for this candidate")

    from app.services.enterprise.onboarding_service import initiate_onboarding_process

    onboarding = await initiate_onboarding_process(
        session=session,
        application_id=request.application_id,
        company_id=current_user.company_id,
        template_id=request.template_id,
        performed_by=f"{current_user.first_name} {current_user.last_name or ''}".strip(),
        background_tasks=background_tasks,
    )

    if not onboarding:
        raise HTTPException(status_code=400, detail="Onboarding already initiated for this candidate")

    # Reload with all relationships for OnboardingResponse
    stmt = (
        select(Onboarding)
        .options(
            selectinload(Onboarding.status),
            selectinload(Onboarding.template),
            selectinload(Onboarding.documents),
            selectinload(Onboarding.activities),
            selectinload(Onboarding.tasks),
            selectinload(Onboarding.notes),
            selectinload(Onboarding.application).selectinload(CandidateApplication.candidate),
            selectinload(Onboarding.application).selectinload(CandidateApplication.job_requirement),
        )
        .where(Onboarding.id == onboarding.id, Onboarding.company_id == current_user.company_id)
    )

    result = await session.execute(stmt)
    onboarding_complete = result.scalar_one_or_none()

    return onboarding_complete


@router.get("/{id}", response_model=OnboardingResponse)
async def get_onboarding(
    id: UUID,
    session: DBSessionDep,
    current_user: Annotated[Any, Depends(PermissionChecker(ModuleScope.onboarding, PermissionAction.read))],
):
    """Get detailed onboarding info."""
    stmt = (
        select(Onboarding)
        .options(
            selectinload(Onboarding.status),
            selectinload(Onboarding.template),
            selectinload(Onboarding.documents),
            selectinload(Onboarding.activities),
            selectinload(Onboarding.tasks),
            selectinload(Onboarding.notes),
            selectinload(Onboarding.application).selectinload(CandidateApplication.candidate),
            selectinload(Onboarding.application).selectinload(CandidateApplication.job_requirement),
        )
        .where(Onboarding.id == id)
    )

    result = await session.execute(stmt)
    onboarding = result.scalar_one_or_none()

    if not onboarding:
        raise HTTPException(status_code=404, detail="Onboarding process not found")

    return onboarding


@router.get("/statuses", response_model=list[OnboardingStatusResponse])
async def get_onboarding_statuses(
    session: DBSessionDep, current_user: Annotated[Any, Depends(get_current_user)]
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
    current_user: Annotated[
        Any, Depends(PermissionChecker(ModuleScope.onboarding, PermissionAction.moderate))
    ],
    background_tasks: BackgroundTasks,
):
    """Request corrections for onboarding (selective rejection)."""
    stmt = (
        select(Onboarding)
        .options(
            selectinload(Onboarding.application).selectinload(CandidateApplication.candidate),
            selectinload(Onboarding.application).selectinload(CandidateApplication.job_requirement),
        )
        .where(Onboarding.id == id, Onboarding.company_id == current_user.company_id)
    )
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
            doc_stmt = select(OnboardingDocument).where(
                OnboardingDocument.id == doc_id, OnboardingDocument.onboarding_id == onboarding.id
            )
            doc_res = await session.execute(doc_stmt)
            doc = doc_res.scalar_one_or_none()
            if doc:
                doc.status = "Rejected"
                doc.comment = request.reason

    # Save rejected fields
    onboarding.rejected_fields = request.rejected_fields or []

    # Log activity
    agent_name = f"{current_user.first_name} {current_user.last_name or ''}".strip()
    desc = f"Correction requested: {request.reason}"
    if request.rejected_document_ids:
        desc += f" ({len(request.rejected_document_ids)} documents rejected)"
    if request.rejected_fields:
        desc += f" ({len(request.rejected_fields)} fields rejected)"

    await log_activity(
        session,
        onboarding.id,
        onboarding.company_id,
        "Correction Requested",
        agent_name,
        {
            "reason": request.reason,
            "rejected_documents": request.rejected_document_ids,
            "rejected_fields": request.rejected_fields,
        },
    )

    # 4. Notify Candidate
    from app.services.enterprise.onboarding_service import send_onboarding_resubmit_email

    await send_onboarding_resubmit_email(
        session=session, onboarding=onboarding, reason=request.reason, background_tasks=background_tasks
    )

    await session.commit()
    return await get_onboarding(id, session, current_user)


@router.post("/{id}/approve", response_model=OnboardingResponse)
async def approve_onboarding(
    id: UUID,
    request: OnboardingApproveRequest,
    session: DBSessionDep,
    current_user: Annotated[
        Any, Depends(PermissionChecker(ModuleScope.onboarding, PermissionAction.moderate))
    ],
    background_tasks: BackgroundTasks,
):
    """Finalize/Approve onboarding and move candidate to Hired."""
    stmt = (
        select(Onboarding)
        .options(
            selectinload(Onboarding.application).selectinload(CandidateApplication.candidate),
            selectinload(Onboarding.application).selectinload(CandidateApplication.job_requirement),
        )
        .where(Onboarding.id == id, Onboarding.company_id == current_user.company_id)
    )
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
        onboarding.application.current_stage = 5  # Standard Hired stage index

        # Trigger any automations for the Hired stage
        from app.services.enterprise.automation_service import trigger_automations

        await trigger_automations(onboarding.application.id, 5, session, background_tasks)

    # 3. Log Activity
    agent_name = f"{current_user.first_name} {current_user.last_name or ''}".strip()
    await log_activity(
        session, onboarding.id, onboarding.company_id, "Onboarding Approved & Finalized", agent_name
    )

    # 4. Notify Candidate (Special Welcome email)
    from app.services.enterprise.onboarding_service import send_onboarding_welcome_email

    await send_onboarding_welcome_email(
        session=session, onboarding=onboarding, background_tasks=background_tasks
    )

    await session.commit()
    return await get_onboarding(id, session, current_user)


@router.patch("/{id}", response_model=OnboardingResponse)
async def update_onboarding(
    id: UUID,
    request: OnboardingUpdateRequest,
    session: DBSessionDep,
    current_user: Annotated[Any, Depends(PermissionChecker(ModuleScope.onboarding, PermissionAction.update))],
):
    """Update onboarding details or status."""
    stmt = select(Onboarding).where(Onboarding.id == id, Onboarding.company_id == current_user.company_id)
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
            agent_name = f"{current_user.first_name} {current_user.last_name or ''}".strip()
            await log_activity(
                session,
                onboarding.id,
                onboarding.company_id,
                f"Onboarding status changed to {new_status.name}",
                agent_name,
            )

            if new_status.name == "Completed":
                onboarding.completed_at = datetime.now()

    for key, value in update_data.items():
        setattr(onboarding, key, value)

    await session.commit()

    # Reload with all relationships for OnboardingResponse
    stmt = (
        select(Onboarding)
        .options(
            selectinload(Onboarding.status),
            selectinload(Onboarding.template),
            selectinload(Onboarding.documents),
            selectinload(Onboarding.activities),
            selectinload(Onboarding.tasks),
            selectinload(Onboarding.notes),
            selectinload(Onboarding.application).selectinload(CandidateApplication.candidate),
            selectinload(Onboarding.application).selectinload(CandidateApplication.job_requirement),
        )
        .where(Onboarding.id == id)
    )

    result = await session.execute(stmt)
    onboarding_complete = result.scalar_one_or_none()

    return onboarding_complete


@router.post("/{id}/notes", response_model=OnboardingNoteResponse)
async def add_note(
    id: UUID,
    request: OnboardingNoteCreate,
    session: DBSessionDep,
    current_user: Annotated[
        Any, Depends(PermissionChecker(ModuleScope.onboarding, PermissionAction.moderate))
    ],
):
    """Add a note to onboarding."""
    stmt = select(Onboarding).where(Onboarding.id == id, Onboarding.company_id == current_user.company_id)
    result = await session.execute(stmt)
    onboarding = result.scalar_one_or_none()

    if not onboarding:
        raise HTTPException(status_code=404, detail="Onboarding not found")

    agent_name = f"{current_user.first_name} {current_user.last_name or ''}".strip()
    note = OnboardingNote(
        onboarding_id=id, content=request.content, author_name=agent_name, company_id=current_user.company_id
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
    current_user: Annotated[
        Any, Depends(PermissionChecker(ModuleScope.onboarding, PermissionAction.moderate))
    ],
):
    """Add a task to onboarding."""
    stmt = select(Onboarding).where(Onboarding.id == id, Onboarding.company_id == current_user.company_id)
    result = await session.execute(stmt)
    onboarding = result.scalar_one_or_none()

    if not onboarding:
        raise HTTPException(status_code=404, detail="Onboarding not found")

    task = OnboardingTask(onboarding_id=id, company_id=current_user.company_id, **request.model_dump())
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


@router.post("/{id}/documents/request", response_model=OnboardingDocumentResponse)
async def request_document(
    id: UUID,
    request: OnboardingDocumentRequest,
    session: DBSessionDep,
    current_user: Annotated[
        Any, Depends(PermissionChecker(ModuleScope.onboarding, PermissionAction.moderate))
    ],
):
    """Request a document from candidate."""
    stmt = select(Onboarding).where(Onboarding.id == id, Onboarding.company_id == current_user.company_id)
    result = await session.execute(stmt)
    onboarding = result.scalar_one_or_none()

    if not onboarding:
        raise HTTPException(status_code=404, detail="Onboarding not found")

    doc = OnboardingDocument(
        onboarding_id=id,
        name=request.name,
        due_date=request.due_date,
        status="Pending",
        company_id=current_user.company_id,
    )
    session.add(doc)

    agent_name = f"{current_user.first_name} {current_user.last_name or ''}".strip()
    await log_activity(
        session, onboarding.id, onboarding.company_id, f"Document request sent: {request.name}", agent_name
    )

    await session.commit()
    await session.refresh(doc)
    return doc
