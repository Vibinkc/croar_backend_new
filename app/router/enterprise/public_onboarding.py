import os
from typing import Annotated, Any
from uuid import UUID

import anyio
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep
from app.models.enterprise.candidate import CandidateApplication
from app.models.enterprise.job import JobRequirement
from app.models.enterprise.onboarding import (
    Onboarding,
    OnboardingActivity,
    OnboardingDocument,
    OnboardingStatus,
)
from app.schemas.enterprise.onboarding import OnboardingResponse

router = APIRouter(prefix="/public/onboarding", tags=["Public Onboarding"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_UPLOAD_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".doc", ".docx", ".txt"}


def _safe_filename(name: str | None, fallback: str = "unnamed") -> str:
    """Strip any path components / traversal so an upload can't escape its directory."""
    import re

    base = os.path.basename((name or "").replace("\\", "/"))
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base).lstrip(".")
    return base or fallback


async def _save_upload(file: UploadFile, upload_dir: str, prefix: str = "") -> str:
    """Validate type, stream to disk with a hard size cap, and a unique sanitized filename."""
    from uuid import uuid4

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_UPLOAD_EXTS:
        raise HTTPException(status_code=400, detail=f"File type '{ext or 'unknown'}' not allowed")

    # Prefix with a short unique id so distinct uploads (even same-named) never overwrite.
    file_path = os.path.join(upload_dir, f"{prefix}{uuid4().hex[:8]}_{_safe_filename(file.filename)}")
    size = 0
    too_large = False
    async with await anyio.open_file(file_path, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                too_large = True
                break
            await f.write(chunk)
    # Remove the partial file only after it's closed (Windows-safe), then signal the error.
    if too_large:
        os.remove(file_path)
        raise HTTPException(status_code=413, detail="File too large (max 10MB)")
    return file_path


@router.get("/{token}", response_model=OnboardingResponse)
async def get_public_onboarding(token: UUID, session: DBSessionDep) -> object:
    """Get onboarding details for candidate using unique token (UUID)."""
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
            selectinload(Onboarding.application)
            .selectinload(CandidateApplication.job_requirement)
            .selectinload(JobRequirement.company),
        )
        .where(Onboarding.id == token)
    )

    result = await session.execute(stmt)
    onboarding = result.scalar_one_or_none()

    if not onboarding:
        raise HTTPException(status_code=404, detail="Onboarding process not found or link expired")

    # Populate branding fields for the schema
    if (
        onboarding.application
        and onboarding.application.job_requirement
        and onboarding.application.job_requirement.company
    ):
        # company_name/company_logo are response-only fields attached dynamically (not ORM columns).
        ob: Any = onboarding
        ob.company_name = onboarding.application.job_requirement.company.name
        ob.company_logo = onboarding.application.job_requirement.company.logo_url

    return onboarding


@router.post("/{token}/submit")
async def submit_onboarding_info(token: UUID, request: Request, session: DBSessionDep) -> dict[str, str]:
    """Submit onboarding information from the public form."""
    stmt = select(Onboarding).where(Onboarding.id == token)
    result = await session.execute(stmt)
    onboarding = result.scalar_one_or_none()

    if not onboarding:
        raise HTTPException(status_code=404, detail="Onboarding process not found")

    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON data") from None

    # Update info blocks based on what's provided
    if "job_info" in data:
        onboarding.job_info = data["job_info"]
    if "personal_info" in data:
        onboarding.personal_info = data["personal_info"]
    if "education_info" in data:
        onboarding.education_info = data["education_info"]
    if "other_info" in data:
        onboarding.other_info = data["other_info"]
    if "form_data" in data:
        onboarding.form_data = data["form_data"]

    # Clear rejected fields as the candidate has resubmitted info
    onboarding.rejected_fields = []

    # Update status to Awaiting Confirmation
    status_stmt = select(OnboardingStatus).where(OnboardingStatus.name == "Awaiting Confirmation")
    res_status = await session.execute(status_stmt)
    awaiting_status = res_status.scalar_one_or_none()
    if awaiting_status:
        onboarding.status_id = awaiting_status.id

    # Log activity
    activity = OnboardingActivity(
        onboarding_id=onboarding.id,
        action="Candidate submitted final onboarding details",
        performed_by="Candidate",
        company_id=onboarding.company_id,
    )
    session.add(activity)

    await session.commit()
    return {"message": "Information submitted successfully"}


@router.post("/{token}/upload/{doc_id}")
async def upload_onboarding_document(
    token: UUID, doc_id: UUID, file: Annotated[UploadFile, File()], session: DBSessionDep
) -> dict[str, str]:
    """Upload a requested document."""
    # 1. Verify onboarding and document link
    stmt = select(OnboardingDocument).where(
        OnboardingDocument.id == doc_id, OnboardingDocument.onboarding_id == token
    )
    result = await session.execute(stmt)
    doc = result.scalar_one_or_none()

    if not doc:
        raise HTTPException(status_code=404, detail="Document request not found")

    # 2. Save file (simplistic version, should use a proper storage service)
    upload_dir = os.path.join("uploads", "onboarding", str(token))
    os.makedirs(upload_dir, exist_ok=True)

    file_path = await _save_upload(file, upload_dir)

    # 3. Update document status
    doc.file_path = file_path
    doc.status = "Received"

    # 4. Log activity
    activity = OnboardingActivity(
        onboarding_id=token,
        action=f"Candidate uploaded document: {doc.name}",
        performed_by="Candidate",
        company_id=doc.company_id,
    )
    session.add(activity)

    await session.commit()
    return {"message": "Document uploaded successfully", "file_path": file_path}


@router.post("/{token}/upload-dynamic/{field_name}")
async def upload_dynamic_onboarding_file(
    token: UUID, field_name: str, file: Annotated[UploadFile, File()], session: DBSessionDep
) -> dict[str, str]:
    """Upload a file for a dynamic field."""
    # Verify onboarding
    stmt = select(Onboarding).where(Onboarding.id == token)
    result = await session.execute(stmt)
    onboarding = result.scalar_one_or_none()

    if not onboarding:
        raise HTTPException(status_code=404, detail="Onboarding process not found")

    # Save file
    upload_dir = os.path.join("uploads", "onboarding", str(token), "dynamic")
    os.makedirs(upload_dir, exist_ok=True)

    file_path = await _save_upload(file, upload_dir, prefix=f"{_safe_filename(field_name)}_")

    # Log activity
    activity = OnboardingActivity(
        onboarding_id=token,
        action=f"Candidate uploaded file for field: {field_name}",
        performed_by="Candidate",
        company_id=onboarding.company_id,
    )
    session.add(activity)

    await session.commit()
    return {"message": "File uploaded successfully", "file_path": file_path}
