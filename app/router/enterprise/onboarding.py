import secrets
import string
from datetime import datetime
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep, PermissionChecker, get_current_user
from app.core.settings import get_settings
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

_settings = get_settings()

router = APIRouter(prefix="/onboarding", tags=["Enterprise Onboarding"])


def generate_onboarding_code() -> str:
    """Generate a unique onboarding code like ONB-XXXXX."""
    suffix = "".join(secrets.choice(string.digits) for _ in range(5))
    return f"ONB-{suffix}"


async def log_activity(
    session: DBSessionDep,
    onboarding_id: UUID,
    company_id: UUID,
    description: str,
    performed_by: str,
    activity_type: str = "SYSTEM",
) -> None:
    activity = OnboardingActivity(
        onboarding_id=onboarding_id,
        company_id=company_id,
        description=description,
        performed_by=performed_by,
        activity_type=activity_type,
    )
    session.add(activity)
    await session.flush()


@router.get("/", response_model=list[OnboardingResponse])
async def list_onboardings(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.onboarding, PermissionAction.read))
    ],
    job_id: UUID | None = None,
    candidate_id: UUID | None = None,
) -> list[Onboarding]:
    """List all onboarding processes."""
    company_id = getattr(current_user, "company_id", None)
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

    stmt = stmt.where(Onboarding.company_id == company_id).order_by(Onboarding.created_at.desc())

    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.post("/initiate", response_model=OnboardingResponse, status_code=201)
async def initiate_onboarding(
    request: OnboardingInitiateRequest,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.onboarding, PermissionAction.create))
    ],
    background_tasks: BackgroundTasks,
) -> Onboarding:
    """Initiate onboarding for a candidate."""
    company_id = getattr(current_user, "company_id", None)
    stmt = (
        select(CandidateApplication)
        .options(selectinload(CandidateApplication.candidate))
        .where(CandidateApplication.id == request.application_id)
    )
    result = await session.execute(stmt)
    application = result.scalar_one_or_none()

    if not application or application.company_id != company_id:
        raise HTTPException(status_code=404, detail="Candidate application not found")

    check_stmt = select(Onboarding).where(
        Onboarding.application_id == request.application_id, Onboarding.company_id == company_id
    )
    res_check = await session.execute(check_stmt)
    if res_check.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Onboarding already initiated for this candidate")

    from app.services.enterprise.onboarding_service import initiate_onboarding_process

    first_name = getattr(current_user, "first_name", "")
    last_name = getattr(current_user, "last_name", "")
    onboarding = await initiate_onboarding_process(
        session=session,
        application_id=request.application_id,
        company_id=cast("UUID", company_id),
        template_id=request.template_id,
        performed_by=f"{first_name} {last_name}".strip(),
        background_tasks=background_tasks,
    )

    if not onboarding:
        raise HTTPException(status_code=400, detail="Onboarding already initiated for this candidate")

    stmt_onb = (
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
        .where(Onboarding.id == onboarding.id, Onboarding.company_id == company_id)
    )

    result_onb = await session.execute(stmt_onb)
    onboarding_complete = result_onb.scalar_one_or_none()

    if not onboarding_complete:
        raise HTTPException(status_code=500, detail="Failed to load created onboarding")

    return onboarding_complete


@router.get("/statuses", response_model=list[OnboardingStatusResponse])
async def get_onboarding_statuses(
    session: DBSessionDep, _current_user: Annotated[object, Depends(get_current_user)]
) -> list[OnboardingStatus]:
    """List all available onboarding statuses.

    Declared BEFORE the /{id} route so the literal path isn't captured as a UUID id.
    """
    stmt = select(OnboardingStatus)
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/{id}", response_model=OnboardingResponse)
async def get_onboarding(
    id: UUID,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.onboarding, PermissionAction.read))
    ],
) -> Onboarding:
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
        .where(Onboarding.id == id, Onboarding.company_id == getattr(current_user, "company_id", None))
    )

    result = await session.execute(stmt)
    onboarding = result.scalar_one_or_none()

    if not onboarding:
        raise HTTPException(status_code=404, detail="Onboarding process not found")

    return onboarding


@router.post("/{id}/resubmit", response_model=OnboardingResponse)
async def resubmit_onboarding(
    id: UUID,
    request: OnboardingResubmitRequest,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.onboarding, PermissionAction.moderate))
    ],
    background_tasks: BackgroundTasks,
) -> Onboarding:
    """Request corrections for onboarding (selective rejection)."""
    company_id = getattr(current_user, "company_id", None)
    stmt = (
        select(Onboarding)
        .options(
            selectinload(Onboarding.application).selectinload(CandidateApplication.candidate),
            selectinload(Onboarding.application).selectinload(CandidateApplication.job_requirement),
        )
        .where(Onboarding.id == id, Onboarding.company_id == company_id)
    )
    result = await session.execute(stmt)
    onboarding = result.scalar_one_or_none()

    if not onboarding:
        raise HTTPException(status_code=404, detail="Onboarding not found")

    status_stmt = select(OnboardingStatus).where(OnboardingStatus.name.in_(["Action Required", "Rejected"]))
    res_status = await session.execute(status_stmt)
    new_status = res_status.scalar_one_or_none()
    if new_status:
        onboarding.status_id = new_status.id

    if request.rejected_document_ids:
        for doc_id in request.rejected_document_ids:
            doc_stmt = select(OnboardingDocument).where(
                OnboardingDocument.id == doc_id, OnboardingDocument.onboarding_id == onboarding.id
            )
            doc_res = await session.execute(doc_stmt)
            doc = doc_res.scalar_one_or_none()
            if doc:
                doc.status = "Rejected"
                doc.rejection_reason = request.reason

    onboarding.rejected_fields = request.rejected_fields or []

    first_name = getattr(current_user, "first_name", "")
    last_name = getattr(current_user, "last_name", "")
    agent_name = f"{first_name} {last_name}".strip()
    desc = f"Correction requested: {request.reason}"

    await log_activity(
        session, onboarding.id, cast("UUID", company_id), desc, agent_name, activity_type="CORRECTION"
    )

    from app.services.enterprise.onboarding_service import send_onboarding_resubmit_email

    await send_onboarding_resubmit_email(
        session=session, onboarding=onboarding, reason=request.reason, background_tasks=background_tasks
    )

    await session.commit()
    return await get_onboarding(id, session, current_user)


@router.post("/{id}/approve", response_model=OnboardingResponse)
async def approve_onboarding(
    id: UUID,
    _request: OnboardingApproveRequest,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.onboarding, PermissionAction.moderate))
    ],
    background_tasks: BackgroundTasks,
) -> Onboarding:
    """Finalize/Approve onboarding and move candidate to Hired."""
    company_id = getattr(current_user, "company_id", None)
    stmt = (
        select(Onboarding)
        .options(
            selectinload(Onboarding.application).selectinload(CandidateApplication.candidate),
            selectinload(Onboarding.application).selectinload(CandidateApplication.job_requirement),
        )
        .where(Onboarding.id == id, Onboarding.company_id == company_id)
    )
    result = await session.execute(stmt)
    onboarding = result.scalar_one_or_none()

    if not onboarding:
        raise HTTPException(status_code=404, detail="Onboarding not found")

    status_stmt = select(OnboardingStatus).where(OnboardingStatus.name == "Completed")
    res_status = await session.execute(status_stmt)
    new_status = res_status.scalar_one_or_none()
    if new_status:
        onboarding.status_id = new_status.id
        onboarding.completed_at = cast("Any", datetime.now())

    if onboarding.application:
        onboarding.application.current_stage = 5
        from app.services.enterprise.automation_service import trigger_automations

        await trigger_automations(onboarding.application.id, 5, session, background_tasks)

    first_name = getattr(current_user, "first_name", "")
    last_name = getattr(current_user, "last_name", "")
    agent_name = f"{first_name} {last_name}".strip()
    await log_activity(
        session, onboarding.id, cast("UUID", company_id), "Onboarding Approved & Finalized", agent_name
    )

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
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.onboarding, PermissionAction.update))
    ],
) -> Onboarding:
    """Update onboarding details or status."""
    company_id = getattr(current_user, "company_id", None)
    stmt = select(Onboarding).where(Onboarding.id == id, Onboarding.company_id == company_id)
    result = await session.execute(stmt)
    onboarding = result.scalar_one_or_none()

    if not onboarding:
        raise HTTPException(status_code=404, detail="Onboarding process not found")

    update_data = request.model_dump(exclude_unset=True)

    if "status_id" in update_data and update_data["status_id"] != onboarding.status_id:
        status_stmt = select(OnboardingStatus).where(OnboardingStatus.id == update_data["status_id"])
        res_status = await session.execute(status_stmt)
        new_status = res_status.scalar_one_or_none()
        if new_status:
            first_name = getattr(current_user, "first_name", "")
            last_name = getattr(current_user, "last_name", "")
            agent_name = f"{first_name} {last_name}".strip()
            await log_activity(
                session,
                onboarding.id,
                cast("UUID", company_id),
                f"Onboarding status changed to {new_status.name}",
                agent_name,
            )

            if new_status.name == "Completed":
                onboarding.completed_at = cast("Any", datetime.now())

    for key, value in update_data.items():
        setattr(onboarding, key, value)

    await session.commit()
    return await get_onboarding(id, session, current_user)


@router.post("/{id}/notes", response_model=OnboardingNoteResponse)
async def add_note(
    id: UUID,
    request: OnboardingNoteCreate,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.onboarding, PermissionAction.moderate))
    ],
) -> OnboardingNote:
    """Add a note to onboarding."""
    company_id = getattr(current_user, "company_id", None)
    stmt = select(Onboarding).where(Onboarding.id == id, Onboarding.company_id == company_id)
    result = await session.execute(stmt)
    onboarding = result.scalar_one_or_none()

    if not onboarding:
        raise HTTPException(status_code=404, detail="Onboarding not found")

    first_name = getattr(current_user, "first_name", "")
    last_name = getattr(current_user, "last_name", "")
    agent_name = f"{first_name} {last_name}".strip()
    note = OnboardingNote(
        onboarding_id=id, content=request.content, author_name=agent_name, company_id=cast("UUID", company_id)
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
        object, Depends(PermissionChecker(ModuleScope.onboarding, PermissionAction.moderate))
    ],
) -> OnboardingTask:
    """Add a task to onboarding."""
    company_id = getattr(current_user, "company_id", None)
    stmt = select(Onboarding).where(Onboarding.id == id, Onboarding.company_id == company_id)
    result = await session.execute(stmt)
    onboarding = result.scalar_one_or_none()

    if not onboarding:
        raise HTTPException(status_code=404, detail="Onboarding not found")

    task = OnboardingTask(onboarding_id=id, company_id=cast("UUID", company_id), **request.model_dump())
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
        object, Depends(PermissionChecker(ModuleScope.onboarding, PermissionAction.moderate))
    ],
) -> OnboardingDocument:
    """Request a document from candidate."""
    company_id = getattr(current_user, "company_id", None)
    stmt = select(Onboarding).where(Onboarding.id == id, Onboarding.company_id == company_id)
    result = await session.execute(stmt)
    onboarding = result.scalar_one_or_none()

    if not onboarding:
        raise HTTPException(status_code=404, detail="Onboarding not found")

    doc = OnboardingDocument(
        onboarding_id=id, name=request.name, status="Pending", company_id=cast("UUID", company_id)
    )
    session.add(doc)

    first_name = getattr(current_user, "first_name", "")
    last_name = getattr(current_user, "last_name", "")
    agent_name = f"{first_name} {last_name}".strip()
    await log_activity(
        session, onboarding.id, cast("UUID", company_id), f"Document request sent: {request.name}", agent_name
    )

    await session.commit()
    await session.refresh(doc)
    return doc
