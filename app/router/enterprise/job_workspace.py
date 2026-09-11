"""Notes and attachments on a job requisition.

These back the job detail page's Notes and Attachments tabs. They share the ``/jobs`` prefix and
the scoping helpers from ``jobs.py`` — kept in their own module only so that file does not grow
another 250 lines.

Notes are deliberately separate from ``JobActivity``: activities are the system-written audit
trail and must never be hand-editable, whereas notes are human commentary that can be edited,
pinned and deleted.
"""

import os
import re
import uuid as uuid_lib
from typing import Annotated, Any, cast
from uuid import UUID

import aiofiles
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import func, select

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.candidate import Candidate, CandidateApplication
from app.models.enterprise.job import JobAttachment, JobNote
from app.models.shared.constants import ModuleScope, PermissionAction
from app.router.enterprise.jobs import _actor_name, _get_scoped_job, _log_job_activity
from app.schemas.enterprise.jobs import (
    AddCandidateToJobIn,
    AddCandidateToJobOut,
    JobAttachmentOut,
    JobNoteIn,
    JobNoteOut,
    JobNotePatch,
    SendApplicationFormIn,
)
from app.services.enterprise import job_inbox as inbox_service

router = APIRouter(prefix="/jobs", tags=["Enterprise Jobs"])


# ── Notes ─────────────────────────────────────────────────────────────────────────────────────


@router.get("/{job_id}/notes", response_model=list[JobNoteOut])
async def list_job_notes(
    job_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.read))],
) -> list[JobNote]:
    """Notes on a requisition — pinned first, then newest first."""
    job = await _get_scoped_job(session, current_user, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    rows = (
        (
            await session.execute(
                select(JobNote)
                .where(JobNote.job_requirement_id == job_id)
                .order_by(JobNote.is_pinned.desc(), JobNote.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


@router.post("/{job_id}/notes", response_model=JobNoteOut, status_code=201)
async def create_job_note(
    job_id: UUID,
    payload: JobNoteIn,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.update))],
) -> JobNote:
    job = await _get_scoped_job(session, current_user, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    note = JobNote(
        job_requirement_id=job.id,
        company_id=job.company_id,
        author_id=getattr(current_user, "id", None),
        author_name=_actor_name(current_user),
        body=payload.body.strip(),
        is_pinned=payload.is_pinned,
    )
    session.add(note)
    _log_job_activity(session, job, current_user, "note_added")
    await session.commit()
    await session.refresh(note)
    return note


@router.patch("/{job_id}/notes/{note_id}", response_model=JobNoteOut)
async def update_job_note(
    job_id: UUID,
    note_id: UUID,
    payload: JobNotePatch,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.update))],
) -> JobNote:
    """Edit a note's text or toggle its pin.

    Only the author may rewrite the text. Anyone who can update the job may pin or unpin, because
    pinning is about what the team should see first, not about authorship.
    """
    job = await _get_scoped_job(session, current_user, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    note = (
        await session.execute(
            select(JobNote).where(JobNote.id == note_id, JobNote.job_requirement_id == job_id)
        )
    ).scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    if payload.body is not None:
        if note.author_id and note.author_id != getattr(current_user, "id", None):
            raise HTTPException(status_code=403, detail="Only the author can edit this note")
        note.body = payload.body.strip()
    if payload.is_pinned is not None:
        note.is_pinned = payload.is_pinned

    await session.commit()
    await session.refresh(note)
    return note


@router.delete("/{job_id}/notes/{note_id}")
async def delete_job_note(
    job_id: UUID,
    note_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.update))],
) -> dict[str, str]:
    job = await _get_scoped_job(session, current_user, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    note = (
        await session.execute(
            select(JobNote).where(JobNote.id == note_id, JobNote.job_requirement_id == job_id)
        )
    ).scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    if note.author_id and note.author_id != getattr(current_user, "id", None):
        raise HTTPException(status_code=403, detail="Only the author can delete this note")
    await session.delete(note)
    await session.commit()
    return {"status": "deleted"}


# ── Attachments ───────────────────────────────────────────────────────────────────────────────

JOB_ATTACHMENT_DIR = "uploads/job_attachments"
# 20 MB — briefs, signed requisition forms, scorecards. Not a place for video.
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
# These files are served back over HTTP from a static mount, so anything a browser might execute
# or render as markup is refused rather than stored.
BLOCKED_ATTACHMENT_EXTS = {
    ".bat",
    ".cmd",
    ".com",
    ".exe",
    ".htm",
    ".html",
    ".jar",
    ".js",
    ".mjs",
    ".msi",
    ".php",
    ".ps1",
    ".scr",
    ".sh",
    ".svg",
    ".vbs",
}


def _safe_attachment_name(raw: str) -> str:
    """Reduce an uploaded filename to a harmless basename.

    Strips any directory component (POSIX and Windows separators alike) and every character
    outside a conservative set, so a crafted name can never escape the upload directory.
    """
    base = os.path.basename((raw or "").replace("\\", "/"))
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", base).lstrip(".")
    return cleaned[:120] or "file"


@router.get("/{job_id}/attachments", response_model=list[JobAttachmentOut])
async def list_job_attachments(
    job_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.read))],
) -> list[JobAttachment]:
    job = await _get_scoped_job(session, current_user, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    rows = (
        (
            await session.execute(
                select(JobAttachment)
                .where(JobAttachment.job_requirement_id == job_id)
                .order_by(JobAttachment.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


@router.post("/{job_id}/attachments", response_model=JobAttachmentOut, status_code=201)
async def upload_job_attachment(
    job_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.update))],
    file: UploadFile = File(...),
) -> JobAttachment:
    """Attach a file to a requisition (brief, signed form, scorecard, …)."""
    job = await _get_scoped_job(session, current_user, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    original = _safe_attachment_name(file.filename or "file")
    ext = os.path.splitext(original)[1].lower()
    if ext in BLOCKED_ATTACHMENT_EXTS:
        raise HTTPException(status_code=400, detail=f"{ext} files cannot be attached")

    # Read into memory so the size is enforced BEFORE anything touches disk. Streaming straight
    # to a file would let an oversized upload fill the volume before the check ran.
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="File is empty")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail="File is larger than 20 MB")

    os.makedirs(JOB_ATTACHMENT_DIR, exist_ok=True)
    # Stored under a random name; the display name lives in the row. Two people uploading
    # "brief.pdf" must not overwrite each other.
    stored = f"{uuid_lib.uuid4().hex}{ext}"
    try:
        # aiofiles, not open(): a 20 MB write on the event loop stalls every other request in
        # flight. Same pattern the public upload routes already use.
        async with aiofiles.open(os.path.join(JOB_ATTACHMENT_DIR, stored), "wb") as fh:
            await fh.write(data)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {e!s}") from e

    row = JobAttachment(
        job_requirement_id=job.id,
        company_id=job.company_id,
        uploader_id=getattr(current_user, "id", None),
        uploader_name=_actor_name(current_user),
        filename=original,
        url=f"/uploads/job_attachments/{stored}",
        content_type=file.content_type,
        size_bytes=len(data),
    )
    session.add(row)
    _log_job_activity(session, job, current_user, "attachment_added", {"filename": original})
    await session.commit()
    await session.refresh(row)
    return row


@router.delete("/{job_id}/attachments/{attachment_id}")
async def delete_job_attachment(
    job_id: UUID,
    attachment_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.update))],
) -> dict[str, str]:
    job = await _get_scoped_job(session, current_user, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    row = (
        await session.execute(
            select(JobAttachment).where(
                JobAttachment.id == attachment_id, JobAttachment.job_requirement_id == job_id
            )
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Attachment not found")

    filename = row.filename
    # basename() again on the way out: the stored url is ours, but re-deriving the path from it
    # without stripping is exactly how a traversal slips back in later.
    stored_path = os.path.join(JOB_ATTACHMENT_DIR, os.path.basename(row.url))
    await session.delete(row)
    _log_job_activity(session, job, current_user, "attachment_removed", {"filename": filename})
    await session.commit()
    # Row first, file second: a leftover file is harmless, a row pointing at a deleted file is a
    # broken download.
    try:
        os.remove(stored_path)
    except OSError:
        pass
    return {"status": "deleted"}


# ── Putting a candidate on a job ──────────────────────────────────────────────────────────────
# Until now the ONLY way an application came into being was someone applying through the public
# form. A recruiter who already had the person in the pool had no way to put them on a job.


@router.post("/{job_id}/candidates", response_model=AddCandidateToJobOut, status_code=201)
async def add_candidate_to_job(
    job_id: UUID,
    payload: AddCandidateToJobIn,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.update))],
) -> AddCandidateToJobOut:
    """Add an existing candidate to this job's pipeline, at the first stage."""
    job = await _get_scoped_job(session, current_user, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # The candidate must belong to the same company as the job. Without this check any candidate
    # id would attach across tenants.
    candidate = (
        await session.execute(
            select(Candidate).where(
                Candidate.id == payload.candidate_id,
                Candidate.company_id == job.company_id,
                Candidate.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    # Adding the same person twice is a no-op, not an error — the recruiter's intent ("this
    # person should be on this job") is already satisfied, and a 409 here just makes the UI
    # explain a failure that isn't one.
    existing = (
        await session.execute(
            select(CandidateApplication).where(
                CandidateApplication.candidate_id == candidate.id,
                CandidateApplication.job_requirement_id == job.id,
                CandidateApplication.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing:
        return AddCandidateToJobOut(
            application_id=existing.id, candidate_id=candidate.id, already_on_job=True
        )

    application = CandidateApplication(
        candidate_id=candidate.id,
        job_requirement_id=job.id,
        status_id=1,  # "Applied" — the seeded first status, same as the public apply path
        current_stage=1,
        source=(payload.source or "Added manually")[:50],
        company_id=job.company_id,
        applied_at=cast("Any", func.now()),
    )
    session.add(application)
    _log_job_activity(
        session, job, current_user, "candidate_added", {"candidate": candidate.full_name or str(candidate.id)}
    )
    await session.commit()
    await session.refresh(application)
    return AddCandidateToJobOut(application_id=application.id, candidate_id=candidate.id)


# ── Creating a candidate from a CV ────────────────────────────────────────────────────────────


@router.post("/{job_id}/candidates/upload", status_code=201)
async def create_candidate_from_resume(
    job_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.update))],
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Create a candidate by parsing an uploaded CV, then put them on this job.

    The parsing, scoring and de-duplication live in job_inbox because applications forwarded
    from a job board have to produce exactly the same record as a CV a recruiter uploads by
    hand. The difference is only who triggered it, and that is the `source`.
    """
    job = await _get_scoped_job(session, current_user, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    data = await file.read()
    try:
        result = await inbox_service.create_candidate_from_cv(
            session, job, file.filename or "resume.pdf", data, "CV Upload"
        )
    except inbox_service.CvParseError as e:
        raise HTTPException(status_code=e.status, detail=e.message) from e

    _log_job_activity(session, job, current_user, "candidate_added", {"candidate": result["full_name"]})
    await session.commit()
    return result


# ── Sending the job's application form to someone ─────────────────────────────────────────────
# /jobs/{id}/invite-candidate can only mail a Candidate already in the pool, because it takes a
# candidate_id. A recruiter with nothing but an email address had no way to ask that person to
# apply. This sends the same apply link to ANY address, so the person fills in the job's own
# application form themselves and arrives in the pipeline as a normal applicant.


@router.post("/{job_id}/send-form")
async def send_application_form(
    job_id: UUID,
    payload: SendApplicationFormIn,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.update))],
) -> dict[str, Any]:
    """Email the job's application form (as an apply link) to an arbitrary address."""
    from fastapi.concurrency import run_in_threadpool

    from app.core.settings import settings
    from app.router.agents import PILOT_TEST_EMAIL, PILOT_TEST_MODE
    from app.router.enterprise.communication import send_smtp_email
    from app.services.enterprise.sourcing import job_sourcing

    job = await _get_scoped_job(session, current_user, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    email = (payload.email or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="An email address is required")

    name = (payload.name or "").strip() or "there"
    apply_url = f"{settings.frontend_url}/jobs/{job.id}"
    recipient = PILOT_TEST_EMAIL if PILOT_TEST_MODE else email
    subject = ("[TEST] " if PILOT_TEST_MODE else "") + f"Apply for {job.title}"
    location_bit = f" in {job.location}" if job.location else ""
    test_banner = (
        "<div style='background:#fff3cd;border:1px solid #ffe69c;padding:10px;border-radius:8px;"
        f"margin-bottom:14px;font-size:13px'>&#129514; <b>TEST EMAIL</b> &mdash; in production this "
        f"would go to <b>{name}</b> &lt;{email}&gt;.</div>"
        if PILOT_TEST_MODE
        else ""
    )
    body = (
        f"{test_banner}<p>Hi {name},</p>"
        f"<p>We would like you to apply for our <strong>{job.title}</strong> role{location_bit}. "
        "The link below opens the application form &mdash; it only takes a few minutes.</p>"
        f'<p><a href="{apply_url}" style="display:inline-block;padding:12px 24px;background:#4f46e5;'
        'color:#fff;text-decoration:none;border-radius:8px;font-weight:bold">Open the application form</a></p>'
        f'<p style="font-size:12px;color:#6b7280">Or paste this into your browser: {apply_url}</p>'
        "<p>Best regards,<br/>Hiring Team</p>"
    )

    try:
        ok, _ = await run_in_threadpool(send_smtp_email, recipient, subject, body, None, None)
    except Exception:
        ok = False

    # Record it in the job's sourcing funnel, exactly as invite-candidate does, so outreach sent
    # this way still shows up on the Profile Sourcing tab instead of vanishing.
    try:
        await run_in_threadpool(
            job_sourcing.record_invites,
            str(job.id),
            str(job.company_id),
            [
                {
                    "full_name": payload.name or email,
                    "email": email,
                    "platform": "Application form",
                    "profile_url": None,
                    "headline": None,
                    "location": None,
                    "invite_status": "sent" if ok else "failed",
                }
            ],
        )
    except Exception:
        pass

    _log_job_activity(session, job, current_user, "form_sent", {"email": email})
    await session.commit()

    return {
        "status": "success" if ok else "failed",
        "sent": bool(ok),
        "apply_url": apply_url,
        "test_mode": PILOT_TEST_MODE,
        "test_email": PILOT_TEST_EMAIL if PILOT_TEST_MODE else None,
    }


# ── Taking a candidate out of a job ───────────────────────────────────────────────────────────
# Two different things, and conflating them loses information:
#
#   Drop   — they are out of the running, but the fact they were considered (and how far they
#            got) is part of the job's history. Sets the seeded "Rejected" status.
#   Remove — they should not be on this job at all: added by mistake, wrong role. Soft-deletes
#            the application so the pipeline forgets it, while the person stays in the pool.
#
# Neither touches the Candidate record itself.

STATUS_APPLIED = 1
STATUS_REJECTED = 6


async def _scoped_application(session: Any, job: Any, application_id: UUID) -> CandidateApplication:
    row = (
        await session.execute(
            select(CandidateApplication).where(
                CandidateApplication.id == application_id,
                CandidateApplication.job_requirement_id == job.id,
                CandidateApplication.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Candidate is not on this job")
    return row


@router.post("/{job_id}/applications/{application_id}/drop")
async def drop_candidate(
    job_id: UUID,
    application_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.update))],
) -> dict[str, Any]:
    """Mark a candidate as out of the running for this job, keeping them on the board."""
    job = await _get_scoped_job(session, current_user, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    application = await _scoped_application(session, job, application_id)
    application.status_id = STATUS_REJECTED
    _log_job_activity(session, job, current_user, "candidate_dropped", {"application": str(application_id)})
    await session.commit()
    return {"status": "dropped", "application_id": str(application_id), "status_id": STATUS_REJECTED}


@router.post("/{job_id}/applications/{application_id}/restore")
async def restore_candidate(
    job_id: UUID,
    application_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.update))],
) -> dict[str, Any]:
    """Undo a drop. Their stage is untouched, so they return where they left off."""
    job = await _get_scoped_job(session, current_user, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    application = await _scoped_application(session, job, application_id)
    application.status_id = STATUS_APPLIED
    _log_job_activity(session, job, current_user, "candidate_restored", {"application": str(application_id)})
    await session.commit()
    return {"status": "restored", "application_id": str(application_id), "status_id": STATUS_APPLIED}


@router.delete("/{job_id}/applications/{application_id}")
async def remove_candidate_from_job(
    job_id: UUID,
    application_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.update))],
) -> dict[str, Any]:
    """Take a candidate off this job entirely. The person stays in the talent pool."""
    job = await _get_scoped_job(session, current_user, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    application = await _scoped_application(session, job, application_id)

    # Soft delete, matching how bulk application deletion already works: a hard DELETE would hit
    # FK violations from assessment attempts, interviews and onboarding rows that reference this
    # application. Every pipeline query already filters deleted_at IS NULL.
    application.deleted_at = cast("Any", func.now())
    _log_job_activity(session, job, current_user, "candidate_removed", {"application": str(application_id)})
    await session.commit()
    return {"status": "removed", "application_id": str(application_id)}
