from datetime import datetime
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import selectinload

from app.core.ai import generate_job_description_ai
from app.core.dependencies import DBSessionDep, PermissionChecker
from app.core.settings import settings
from app.models.enterprise.assessment import AssessmentAutomation
from app.models.enterprise.candidate import Candidate, CandidateApplication
from app.models.enterprise.communication import MailAutomation
from app.models.enterprise.company import Company
from app.models.enterprise.interview import InterviewAutomation
from app.models.enterprise.job import JobPosting, JobRequirement
from app.models.enterprise.onboarding import OnboardingAutomation
from app.models.shared.constants import ModuleScope, PermissionAction
from app.schemas.enterprise.jobs import (
    JDGenerationRequest,
    JobMetrics,
    JobRequirementCreate,
    JobRequirementResponse,
    JobRequirementUpdate,
    JobStageResponse,
    PublishJobRequest,
    WorkflowGenerationRequest,
)
from app.services.enterprise.google_jobs import google_jobs_service
from app.services.enterprise.hiring_agent import hiring_agent_service

router = APIRouter(prefix="/jobs", tags=["Enterprise Jobs"])

# Removed get_enterprise_agent helper as it's redundant with PermissionChecker


def normalize_workflow_stages(stages: list[dict[str, object]]) -> list[dict[str, object]]:
    """Ensure stage IDs are sequential strings 1, 2, 3..."""
    if not stages:
        return stages
    for i, stage in enumerate(stages):
        stage["id"] = str(i + 1)
    return stages


async def _get_scoped_job(session: Any, current_user: object, job_id: UUID) -> JobRequirement | None:
    """Fetch a live (non-deleted) job the caller is allowed to act on.

    A consultancy may manage its OWN jobs and its partners' (companies whose `parent_id`
    is the consultancy). Everyone else is scoped to their own company. Returns None when the
    job doesn't exist or is out of scope — callers turn that into a 404. Used by the mutating
    endpoints so that "can view" (list/get) and "can manage" (update/delete/publish) stay in
    sync — previously a consultancy could open a partner job but got 404 on edit/delete/publish.
    """
    cid = getattr(current_user, "company_id", None)
    is_consultancy = getattr(getattr(current_user, "company", None), "is_consultancy", False)

    stmt = select(JobRequirement).where(JobRequirement.id == job_id, JobRequirement.deleted_at.is_(None))
    if is_consultancy:
        partner_ids = (
            (await session.execute(select(Company.id).where(Company.parent_id == cid))).scalars().all()
        )
        stmt = stmt.where(or_(JobRequirement.company_id == cid, JobRequirement.company_id.in_(partner_ids)))
    else:
        stmt = stmt.where(JobRequirement.company_id == cid)

    return (await session.execute(stmt)).scalar_one_or_none()


@router.post("/", response_model=JobRequirementResponse)
async def create_job(
    request: JobRequirementCreate,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.create))],
) -> JobRequirement:
    """Create a new job requirement."""
    is_consultancy = getattr(getattr(current_user, "company", None), "is_consultancy", False)
    target_company_id = request.company_id or getattr(current_user, "company_id", None)
    workflow_stages = normalize_workflow_stages(request.workflow_stages or [])

    # Validation: If consultancy, they can hire for partners.
    # If not, it must be their own company.
    if target_company_id != getattr(current_user, "company_id", None):
        if not is_consultancy:
            raise HTTPException(status_code=403, detail="Not authorized to hire for other organizations.")
        # Verify it's a partner
        partner_stmt = select(Company).where(
            Company.id == target_company_id, Company.parent_id == getattr(current_user, "company_id", None)
        )
        partner = (await session.execute(partner_stmt)).scalar_one_or_none()
        if not partner:
            raise HTTPException(status_code=403, detail="Target company is not a registered partner node.")

    new_job = JobRequirement(
        **request.model_dump(exclude={"target_platforms", "workflow_stages", "company_id"}),
        workflow_stages=workflow_stages,
        company_id=target_company_id,
    )
    session.add(new_job)
    await session.commit()
    await session.refresh(new_job)

    # Eager load for response
    stmt = (
        select(JobRequirement)
        .options(selectinload(JobRequirement.postings), selectinload(JobRequirement.company))
        .where(JobRequirement.id == new_job.id)
    )
    result = await session.execute(stmt)
    return result.scalar_one()


@router.get("/", response_model=list[JobRequirementResponse])
async def list_jobs(
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.read))],
    company_id: UUID | None = None,
) -> list[JobRequirement]:
    """List all jobs (optionally filtered by partner company)."""
    from sqlalchemy import or_

    is_consultancy = getattr(getattr(current_user, "company", None), "is_consultancy", False)

    stmt = (
        select(JobRequirement)
        .options(selectinload(JobRequirement.postings), selectinload(JobRequirement.company))
        .where(JobRequirement.deleted_at.is_(None))
    )

    if company_id:
        # User explicitly requested a specific company
        if company_id != getattr(current_user, "company_id", None):
            if not is_consultancy:
                raise HTTPException(status_code=403, detail="Access denied.")
            # Verify partner
            partner_stmt = select(Company).where(
                Company.id == company_id, Company.parent_id == getattr(current_user, "company_id", None)
            )
            if not (await session.execute(partner_stmt)).scalar_one_or_none():
                raise HTTPException(status_code=403, detail="Invalid partner context.")
        stmt = stmt.where(JobRequirement.company_id == company_id)
    else:
        # Default view
        if is_consultancy:
            # Show jobs for the consultancy AND all its partners
            partner_ids_stmt = select(Company.id).where(
                Company.parent_id == getattr(current_user, "company_id", None)
            )
            partner_ids = (await session.execute(partner_ids_stmt)).scalars().all()
            stmt = stmt.where(
                or_(
                    JobRequirement.company_id == getattr(current_user, "company_id", None),
                    JobRequirement.company_id.in_(partner_ids),
                )
            )
        else:
            stmt = stmt.where(JobRequirement.company_id == getattr(current_user, "company_id", None))

    result = await session.execute(stmt.order_by(JobRequirement.created_at.desc()))
    jobs = result.scalars().all()
    return list(jobs)


@router.get("/{job_id}", response_model=JobRequirementResponse)
async def get_job(
    job_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.read))],
) -> JobRequirementResponse:
    from sqlalchemy import or_

    is_consultancy = getattr(getattr(current_user, "company", None), "is_consultancy", False)

    stmt = (
        select(JobRequirement)
        .options(selectinload(JobRequirement.postings), selectinload(JobRequirement.company))
        .where(JobRequirement.id == job_id, JobRequirement.deleted_at.is_(None))
    )

    if is_consultancy:
        # Allow if job belongs to consultancy OR any of its partners
        partner_ids_stmt = select(Company.id).where(
            Company.parent_id == getattr(current_user, "company_id", None)
        )
        partner_ids = (await session.execute(partner_ids_stmt)).scalars().all()
        stmt = stmt.where(
            or_(
                JobRequirement.company_id == getattr(current_user, "company_id", None),
                JobRequirement.company_id.in_(partner_ids),
            )
        )
    else:
        stmt = stmt.where(JobRequirement.company_id == getattr(current_user, "company_id", None))

    result = await session.execute(stmt)
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    def _to_int(value: Any, default: int = 0) -> int:
        # Stage ids are usually ints but may be missing/non-numeric in stored JSON.
        try:
            return int(cast("Any", value))
        except (TypeError, ValueError):
            return default

    # Per-stage counts drive the pipeline tabs. `current_stage` is the workflow-stage index,
    # so this is the RIGHT key for the stage cards. Exclude soft-deleted applications — otherwise
    # deleted candidates keep inflating the stage counts (same bug the dashboard had).
    stage_stmt = (
        select(CandidateApplication.current_stage, func.count(CandidateApplication.id))
        .where(CandidateApplication.job_requirement_id == job_id, CandidateApplication.deleted_at.is_(None))
        .group_by(CandidateApplication.current_stage)
    )
    stage_result = await session.execute(stage_stmt)
    stage_counts: dict[int, int] = {}
    for stage, count in stage_result.all():
        stage_counts[_to_int(stage)] = _to_int(count)

    # The summary metrics (interviews / rejected / onboarded) are STATUS concepts, not stage
    # indices — counting them off `current_stage` was wrong (e.g. "rejected" read stage 6, which
    # doesn't exist in a 5-stage pipeline, so it was always 0). Group by `status_id` instead.
    # application_statuses: 1 Applied · 2 Screening · 3 Interviewing · 4 Offered · 5 Hired ·
    # 6 Rejected · 7 Withdrawn.
    status_stmt = (
        select(CandidateApplication.status_id, func.count(CandidateApplication.id))
        .where(CandidateApplication.job_requirement_id == job_id, CandidateApplication.deleted_at.is_(None))
        .group_by(CandidateApplication.status_id)
    )
    status_result = await session.execute(status_stmt)
    status_counts: dict[int, int] = {}
    for status_id, count in status_result.all():
        status_counts[_to_int(status_id)] = _to_int(count)

    # Dynamic Stages (Rounds)
    stages_to_use = job.workflow_stages or []

    # Explicitly construct the response to ensure stages are included
    response = JobRequirementResponse.model_validate(job)

    response.stages = [
        JobStageResponse(
            id=_to_int(s.get("id", i + 1), i + 1),
            name=str(s.get("name", f"Stage {i + 1}")),
            count=stage_counts.get(_to_int(s.get("id", i + 1), i + 1), 0),
        )
        for i, s in enumerate(stages_to_use)
    ]

    # Metrics: submitted = everyone who applied; pipeline = still-active (not Hired/Rejected/
    # Withdrawn); the rest map directly to their status.
    total_apps = sum(status_counts.values())
    terminal = status_counts.get(5, 0) + status_counts.get(6, 0) + status_counts.get(7, 0)
    response.metrics = JobMetrics(
        pipeline=total_apps - terminal,
        submitted=total_apps,
        interviews=status_counts.get(3, 0),
        rejected=status_counts.get(6, 0),
        onboarded=status_counts.get(5, 0),
    )

    return response


@router.patch("/{job_id}", response_model=JobRequirementResponse)
async def update_job(
    job_id: UUID,
    request: JobRequirementUpdate,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.update))],
) -> JobRequirement:
    """Update an existing job requisition."""
    job = await _get_scoped_job(session, current_user, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    update_data = request.model_dump(exclude_unset=True)
    if update_data.get("workflow_stages"):
        update_data["workflow_stages"] = normalize_workflow_stages(
            cast("list[dict[str, object]]", update_data["workflow_stages"])
        )

    for key, value in update_data.items():
        setattr(job, key, value)

    await session.commit()
    await session.refresh(job)

    # Re-fetch with mappings. Scope by the job's OWN company_id (already access-checked above),
    # not the caller's — otherwise a consultancy editing a partner job would fail the reload.
    stmt_reload = (
        select(JobRequirement)
        .options(selectinload(JobRequirement.postings), selectinload(JobRequirement.company))
        .where(JobRequirement.id == job_id)
    )
    result_reload = await session.execute(stmt_reload)
    return result_reload.scalar_one()


@router.delete("/{job_id}")
async def delete_job(
    job_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.delete))],
) -> dict[str, str]:
    """Soft delete a job requisition."""
    job = await _get_scoped_job(session, current_user, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # 1. Hard-delete ALL automations tied to this job (assessment, mail, interview, onboarding).
    await session.execute(
        delete(AssessmentAutomation).where(AssessmentAutomation.job_requirement_id == job_id)
    )
    await session.execute(delete(MailAutomation).where(MailAutomation.job_requirement_id == job_id))
    await session.execute(delete(InterviewAutomation).where(InterviewAutomation.job_requirement_id == job_id))
    await session.execute(
        delete(OnboardingAutomation).where(OnboardingAutomation.job_requirement_id == job_id)
    )

    # 2. Soft-delete every application for this job EXCEPT hired candidates (status_id == 5),
    #    so hired people keep their records (onboarding, employee profile) intact.
    hired_status_id = 5
    await session.execute(
        update(CandidateApplication)
        .where(
            CandidateApplication.job_requirement_id == job_id,
            CandidateApplication.status_id != hired_status_id,
            CandidateApplication.deleted_at.is_(None),
        )
        .values(deleted_at=datetime.now())
    )

    # 3. Soft-delete the job requirement itself.
    job.deleted_at = cast("Any", datetime.now())
    await session.commit()

    # 3. Notify Google Jobs of deletion
    job_url = f"{settings.frontend_url}/jobs/{job_id}"
    await google_jobs_service.notify_job_update(job_url, update_type="URL_DELETED")

    return {"message": "Job and related automations deleted successfully"}


@router.post("/{job_id}/publish")
async def publish_job(
    job_id: UUID,
    request: PublishJobRequest,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.publish))],
) -> dict[str, str]:
    """Publish a job to specific platforms."""
    job = await _get_scoped_job(session, current_user, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    for platform in request.platforms:
        stmt_posting = select(JobPosting).where(
            JobPosting.job_requirement_id == job_id, JobPosting.platform == platform
        )
        result_posting = await session.execute(stmt_posting)
        existing_posting = result_posting.scalar_one_or_none()

        if existing_posting:
            existing_posting.status = "PUBLISHED"
            existing_posting.posted_at = cast("Any", datetime.now())
        else:
            new_posting = JobPosting(
                job_requirement_id=job_id,
                platform=platform,
                status="PUBLISHED",
                posted_at=cast("Any", datetime.now()),
                company_id=job.company_id,
            )
            session.add(new_posting)

    await session.commit()

    # Notify Google Jobs if selected
    if "Google Jobs" in request.platforms:
        job_url = f"{settings.frontend_url}/jobs/{job_id}"
        await google_jobs_service.notify_job_update(job_url, update_type="URL_UPDATED")

    return {"message": f"Job published to {len(request.platforms)} platforms"}


@router.post("/generate-jd")
async def generate_jd_endpoint(
    request: JDGenerationRequest,
    _current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.generate))],
) -> dict[str, object]:
    """Generate or enhance a job description and optionally a workflow using AI."""
    jd_result = await generate_job_description_ai(
        title=request.title,
        existing_description=request.existing_description or "",
        location=request.location or "",
        experience_min=request.experience_min or "",
        experience_max=request.experience_max or "",
        additional_instructions=request.additional_instructions or "",
    )

    workflow: list[dict[str, object]] = []
    if request.generate_workflow:
        workflow = await hiring_agent_service.generate_automated_workflow(
            job_title=request.title, job_description=cast("str", jd_result.get("description", ""))
        )

    return {**jd_result, "suggested_workflow": workflow}


@router.post("/generate-workflow")
async def generate_workflow_endpoint(
    request: WorkflowGenerationRequest,
    _current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.generate))],
) -> list[dict[str, object]]:
    """Generate a structured automated workflow for a job."""
    workflow = await hiring_agent_service.generate_automated_workflow(
        job_title=request.title, job_description=request.description
    )
    return workflow


@router.get("/{job_id}/sourced-candidates")
async def get_sourced_candidates(
    job_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.read))],
) -> dict[str, Any]:
    """The job's Profile Sourcing funnel: everyone Croar Pilot sourced + invited for this job, each
    one's outreach-mail status, and whether they've since applied (filled the form → in the pipeline)."""
    from fastapi.concurrency import run_in_threadpool

    from app.services.enterprise.sourcing import job_sourcing

    job = await _get_scoped_job(session, current_user, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    rows = await run_in_threadpool(job_sourcing.list_for_job, str(job_id))
    invited = len(rows)
    mail_sent = sum(1 for r in rows if r.get("invite_status") == "sent")
    applied = sum(1 for r in rows if r.get("applied"))
    return {
        "job_id": str(job_id),
        "candidates": rows,
        "summary": {
            "invited": invited,
            "mail_sent": mail_sent,
            "mail_failed": invited - mail_sent,
            "applied": applied,
            "awaiting": mail_sent - applied,
        },
    }


@router.get("/{job_id}/matching-candidates")
async def get_matching_candidates(
    job_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.read))],
    limit: int = 20,
) -> dict[str, Any]:
    """Candidate-Bank people whose skills fit THIS job, ranked best-first — for the job's
    "Candidate Bank" tab, so you can reach out to people you already have for this role."""
    from fastapi.concurrency import run_in_threadpool

    from app.services.enterprise.skill_match import overlap
    from app.services.enterprise.sourcing import job_sourcing

    job = await _get_scoped_job(session, current_user, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    cands = list(
        (
            await session.execute(
                select(Candidate).where(
                    Candidate.company_id == job.company_id, Candidate.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    scored: list[tuple[int, float, Candidate, list[str]]] = []
    for c in cands:
        cnt, matched, pct = overlap(c.skills, job.required_skills)
        if cnt > 0:  # only surface people who actually share a skill with the role
            scored.append((cnt, pct, c, matched))
    scored.sort(key=lambda t: (-t[0], -t[1]))
    scored = scored[:limit]

    top_ids = [c.id for _, _, c, _ in scored]
    applied_ids: set[Any] = set()
    if top_ids:
        rows = await session.execute(
            select(CandidateApplication.candidate_id).where(
                CandidateApplication.job_requirement_id == job.id,
                CandidateApplication.candidate_id.in_(top_ids),
                CandidateApplication.deleted_at.is_(None),
            )
        )
        applied_ids = {r[0] for r in rows.all()}
    invited_emails: set[str] = set()
    try:
        funnel = await run_in_threadpool(job_sourcing.list_for_job, str(job.id))
        invited_emails = {(r.get("email") or "").lower() for r in funnel if r.get("email")}
    except Exception:
        invited_emails = set()

    return {
        "job_id": str(job.id),
        "job_title": job.title,
        "candidates": [
            {
                "id": str(c.id),
                "full_name": c.full_name,
                "email": c.email,
                "skills": c.skills or [],
                "matched_skills": matched,
                "match_count": cnt,
                "match_pct": pct,
                "already_applied": c.id in applied_ids,
                "already_invited": (c.email or "").lower() in invited_emails,
            }
            for cnt, pct, c, matched in scored
        ],
    }


class InviteCandidateBody(BaseModel):
    candidate_id: UUID


@router.post("/{job_id}/invite-candidate")
async def invite_candidate_to_job(
    job_id: UUID,
    body: InviteCandidateBody,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.read))],
) -> dict[str, Any]:
    """Email a specific Candidate-Bank person to invite them to apply to THIS job (with an apply
    link). Records the outreach in the job's Sourcing funnel so it shows on the Profile Sourcing tab."""
    from fastapi.concurrency import run_in_threadpool

    from app.router.agents import PILOT_TEST_EMAIL, PILOT_TEST_MODE
    from app.router.enterprise.communication import send_smtp_email
    from app.services.enterprise.skill_match import overlap
    from app.services.enterprise.sourcing import job_sourcing

    job = await _get_scoped_job(session, current_user, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    candidate = (
        await session.execute(
            select(Candidate).where(Candidate.id == body.candidate_id, Candidate.company_id == job.company_id)
        )
    ).scalar_one_or_none()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found in this organization.")
    real_email = (candidate.email or "").strip()
    if not real_email:
        raise HTTPException(
            status_code=400, detail="This candidate has no email on record, so they can't be invited."
        )

    _cnt, matched, _pct = overlap(candidate.skills, job.required_skills)
    name = (candidate.full_name or "there").strip()
    apply_url = f"{settings.frontend_url}/jobs/{job.id}"
    recipient = PILOT_TEST_EMAIL if PILOT_TEST_MODE else real_email
    subject = ("[TEST] " if PILOT_TEST_MODE else "") + f"You're invited to apply: {job.title}"
    location_bit = f" in {job.location}" if job.location else ""
    skills_bit = f" Your experience with {', '.join(matched[:4])} stood out to us." if matched else ""
    test_banner = (
        "<div style='background:#fff3cd;border:1px solid #ffe69c;padding:10px;border-radius:8px;"
        f"margin-bottom:14px;font-size:13px'>🧪 <b>TEST EMAIL</b> — in production this would go to "
        f"<b>{name}</b> &lt;{real_email}&gt;.</div>"
        if PILOT_TEST_MODE
        else ""
    )
    email_body = (
        f"{test_banner}<p>Hi {name},</p>"
        f"<p>We came across your profile in our talent bank and think you could be a great fit for our "
        f"<strong>{job.title}</strong> role{location_bit}.{skills_bit}</p>"
        f'<p><a href="{apply_url}" style="display:inline-block;padding:12px 24px;background:#4f46e5;'
        'color:#fff;text-decoration:none;border-radius:8px;font-weight:bold">Apply now</a></p>'
        "<p>Best regards,<br/>Hiring Team</p>"
    )
    try:
        ok, _ = await run_in_threadpool(send_smtp_email, recipient, subject, email_body, None, None)
    except Exception:
        ok, _ = False, "send failed"

    try:
        await run_in_threadpool(
            job_sourcing.record_invites,
            str(job.id),
            str(job.company_id),
            [
                {
                    "full_name": candidate.full_name,
                    "email": real_email,
                    "platform": "Candidate Bank",
                    "profile_url": None,
                    "headline": None,
                    "location": None,
                    "invite_status": "sent" if ok else "failed",
                }
            ],
        )
    except Exception:
        pass

    return {
        "status": "success" if ok else "failed",
        "sent": bool(ok),
        "test_mode": PILOT_TEST_MODE,
        "test_email": PILOT_TEST_EMAIL if PILOT_TEST_MODE else None,
    }


class SendSourcedInviteBody(BaseModel):
    email: str | None = None
    profile_url: str | None = None
    full_name: str | None = None


@router.post("/{job_id}/send-sourced-invite")
async def send_sourced_invite(
    job_id: UUID,
    body: SendSourcedInviteBody,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.read))],
) -> dict[str, Any]:
    """Email the apply invite to a candidate that was SHORTLISTED from Profile Sourcing (a Mongo
    sourced row — no Candidate DB record needed), then flip their Profile Sourcing status to 'sent'.
    While testing, the mail is redirected to PILOT_TEST_EMAIL (see PILOT_TEST_MODE)."""
    from fastapi.concurrency import run_in_threadpool

    from app.router.agents import PILOT_TEST_EMAIL, PILOT_TEST_MODE
    from app.router.enterprise.communication import send_smtp_email
    from app.services.enterprise.sourcing import job_sourcing

    job = await _get_scoped_job(session, current_user, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    real_email = (body.email or "").strip()
    # In production we need a real address to send to. In TEST mode the mail is redirected to the
    # test inbox, so a candidate with no email on record (e.g. sourced from arxiv) can still be sent
    # a test invite — the recruiter is just exercising the flow.
    if not real_email and not PILOT_TEST_MODE:
        raise HTTPException(
            status_code=400, detail="This candidate has no email on record, so they can't be emailed."
        )

    name = (body.full_name or "there").strip()
    apply_url = f"{settings.frontend_url}/jobs/{job.id}"
    recipient = PILOT_TEST_EMAIL if PILOT_TEST_MODE else real_email
    subject = ("[TEST] " if PILOT_TEST_MODE else "") + f"You're invited to apply: {job.title}"
    location_bit = f" in {job.location}" if job.location else ""
    would_go_to = f"&lt;{real_email}&gt;" if real_email else "(no email on record)"
    test_banner = (
        "<div style='background:#fff3cd;border:1px solid #ffe69c;padding:10px;border-radius:8px;"
        f"margin-bottom:14px;font-size:13px'>🧪 <b>TEST EMAIL</b> — in production this would go to "
        f"<b>{name}</b> {would_go_to}.</div>"
        if PILOT_TEST_MODE
        else ""
    )
    email_body = (
        f"{test_banner}<p>Hi {name},</p>"
        f"<p>We came across your profile and think you could be a great fit for our "
        f"<strong>{job.title}</strong> role{location_bit}. We'd love for you to apply.</p>"
        f'<p><a href="{apply_url}" style="display:inline-block;padding:12px 24px;background:#4f46e5;'
        'color:#fff;text-decoration:none;border-radius:8px;font-weight:bold">Apply now</a></p>'
        "<p>Best regards,<br/>Hiring Team</p>"
    )
    try:
        ok, _err = await run_in_threadpool(send_smtp_email, recipient, subject, email_body, None, None)
    except Exception:
        ok = False

    try:
        await run_in_threadpool(
            job_sourcing.mark_invite_sent, str(job.id), real_email, body.profile_url, bool(ok)
        )
    except Exception:
        pass

    return {
        "status": "success" if ok else "failed",
        "sent": bool(ok),
        "test_mode": PILOT_TEST_MODE,
        "test_email": PILOT_TEST_EMAIL if PILOT_TEST_MODE else None,
    }
