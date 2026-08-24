from datetime import datetime
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
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
from app.models.enterprise.job import JobActivity, JobCollaborator, JobPosting, JobRequirement
from app.models.enterprise.onboarding import OnboardingAutomation
from app.models.enterprise.user_role import EnterpriseUser
from app.models.shared.constants import ModuleScope, PermissionAction
from app.router.enterprise.job_portals import active_portal_connection
from app.schemas.enterprise.jobs import (
    AssignJobRequest,
    JDGenerationRequest,
    JobActivityOut,
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
from app.services.enterprise.job_distribution import PublishContext, job_distribution_service

router = APIRouter(prefix="/jobs", tags=["Enterprise Jobs"])

# Removed get_enterprise_agent helper as it's redundant with PermissionChecker


def normalize_workflow_stages(stages: list[dict[str, object]]) -> list[dict[str, object]]:
    """Ensure stage IDs are sequential strings 1, 2, 3..."""
    if not stages:
        return stages
    for i, stage in enumerate(stages):
        stage["id"] = str(i + 1)
    return stages


def _actor_name(user: object) -> str:
    fn = f"{getattr(user, 'first_name', '') or ''} {getattr(user, 'last_name', '') or ''}".strip()
    return fn or str(getattr(user, "email", "") or "Someone")


async def _log_job_activity(
    session: Any, job: JobRequirement, actor: object, action: str, detail: dict[str, Any] | None = None
) -> None:
    """Append an entry to a requisition's audit trail (best-effort — never blocks the action)."""
    try:
        session.add(
            JobActivity(
                job_requirement_id=job.id,
                company_id=job.company_id,
                actor_id=getattr(actor, "id", None),
                actor_name=_actor_name(actor),
                action=action,
                detail=detail,
            )
        )
    except Exception:
        pass


async def _company_member_ids(session: Any, company_id: object) -> set:
    """User ids that belong to this company (to validate owner/collaborator assignments)."""
    rows = (
        (
            await session.execute(
                select(EnterpriseUser.id).where(
                    EnterpriseUser.company_id == company_id, EnterpriseUser.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    return set(rows)


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

    actor_id = getattr(current_user, "id", None)
    new_job = JobRequirement(
        **request.model_dump(exclude={"target_platforms", "workflow_stages", "company_id"}),
        workflow_stages=workflow_stages,
        company_id=target_company_id,
        # The creator becomes the accountable owner by default (Team Management).
        owner_id=actor_id,
        created_by=actor_id,
    )
    session.add(new_job)
    await session.flush()
    await _log_job_activity(session, new_job, current_user, "created", {"title": new_job.title})
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
    mine: bool = False,
) -> list[JobRequirement]:
    """List all jobs (optionally filtered by partner company, or to the caller's own via `mine`)."""
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

    # "My Jobs": requisitions the caller owns OR collaborates on.
    if mine:
        uid = getattr(current_user, "id", None)
        collab_subq = select(JobCollaborator.job_requirement_id).where(JobCollaborator.user_id == uid)
        stmt = stmt.where(or_(JobRequirement.owner_id == uid, JobRequirement.id.in_(collab_subq)))

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

    # Access tracking: stamp last-viewed and log a throttled 'viewed' activity (once/hour/user).
    uid = getattr(current_user, "id", None)
    now = datetime.now()
    last_view = (
        await session.execute(
            select(JobActivity.created_at)
            .where(
                JobActivity.job_requirement_id == job.id,
                JobActivity.actor_id == uid,
                JobActivity.action == "viewed",
            )
            .order_by(JobActivity.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    job.last_viewed_at = cast("Any", now)
    job.last_viewed_by = cast("Any", uid)
    if not last_view or (now - cast("Any", last_view)).total_seconds() > 3600:
        await _log_job_activity(session, job, current_user, "viewed")
    await session.commit()

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

    changed = [k for k in update_data if k not in ("workflow_stages", "application_fields")]
    await _log_job_activity(session, job, current_user, "updated", {"fields": changed} if changed else None)
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


@router.post("/{job_id}/assign", response_model=JobRequirementResponse)
async def assign_job(
    job_id: UUID,
    request: AssignJobRequest,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.assign))],
) -> JobRequirement:
    """Set a requisition's owner and collaborators (Team Management)."""
    job = await _get_scoped_job(session, current_user, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    valid_ids = await _company_member_ids(session, job.company_id)
    prev_owner = job.owner_id

    if request.owner_id is not None:
        if request.owner_id not in valid_ids:
            raise HTTPException(status_code=422, detail="Owner must be a member of this company.")
        job.owner_id = cast("Any", request.owner_id)

    # Replace the collaborator set (dedup, valid members only, never the owner).
    desired = [
        uid for uid in dict.fromkeys(request.collaborator_ids) if uid in valid_ids and uid != job.owner_id
    ]
    await session.execute(delete(JobCollaborator).where(JobCollaborator.job_requirement_id == job_id))
    actor_id = getattr(current_user, "id", None)
    for uid in desired:
        session.add(JobCollaborator(job_requirement_id=job_id, user_id=uid, added_by=actor_id))

    # Resolve names for a readable audit entry.
    involved = set(desired)
    if request.owner_id is not None:
        involved.add(request.owner_id)
    name_map: dict[Any, str] = {}
    if involved:
        rows = (
            await session.execute(
                select(
                    EnterpriseUser.id,
                    EnterpriseUser.first_name,
                    EnterpriseUser.last_name,
                    EnterpriseUser.email,
                ).where(EnterpriseUser.id.in_(involved))
            )
        ).all()
        for r in rows:
            name_map[r[0]] = f"{r[1] or ''} {r[2] or ''}".strip() or r[3]

    detail: dict[str, Any] = {"collaborators": [name_map.get(u) for u in desired]}
    if request.owner_id is not None and request.owner_id != prev_owner:
        detail["owner"] = name_map.get(request.owner_id)
    await _log_job_activity(session, job, current_user, "assigned", detail)
    await session.commit()

    reload = (
        await session.execute(
            select(JobRequirement)
            .options(selectinload(JobRequirement.postings), selectinload(JobRequirement.company))
            .where(JobRequirement.id == job_id)
        )
    ).scalar_one()
    return reload


@router.get("/{job_id}/activity", response_model=list[JobActivityOut])
async def job_activity(
    job_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.read))],
    limit: int = 60,
) -> list[JobActivity]:
    """The audit trail for one requisition (most recent first)."""
    job = await _get_scoped_job(session, current_user, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    rows = (
        (
            await session.execute(
                select(JobActivity)
                .where(JobActivity.job_requirement_id == job_id)
                .order_by(JobActivity.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


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
    http_request: Request,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.publish))],
) -> dict[str, Any]:
    """Distribute a job to the selected external portals via the job-distribution registry.

    Real, standards-based providers (Google for Jobs, Indeed feed, JP aggregators) do live
    work; credentialed portals (Wanted) queue via the company's stored connection; partner
    portals are surfaced honestly as PARTNER_REQUIRED. Each portal's outcome is persisted on
    a JobPosting row and returned per-platform.
    """
    job = await _get_scoped_job(session, current_user, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    company = getattr(job, "company", None)
    if company is None and job.company_id:
        company = (
            await session.execute(select(Company).where(Company.id == job.company_id))
        ).scalar_one_or_none()

    job_url = f"{settings.frontend_url}/jobs/{job_id}"
    feed_base = str(http_request.base_url).rstrip("/")
    company_id = str(job.company_id or "")

    results: list[dict[str, Any]] = []
    for platform in request.platforms:
        meta = job_distribution_service.meta(platform)
        creds: dict[str, Any] = {}
        if meta and meta.requires_credentials and company_id:
            conn = active_portal_connection(company_id, meta.key)
            if conn:
                creds = conn.get("credentials") or {}

        ctx = PublishContext(job=job, company=company, job_url=job_url, feed_url_base=feed_base, creds=creds)
        result = await job_distribution_service.publish(platform, ctx)
        results.append(result.as_dict())

        # Persist the outcome on a per-(job, portal) JobPosting row.
        key = job_distribution_service.resolve_key(platform)
        existing = (
            await session.execute(
                select(JobPosting).where(JobPosting.job_requirement_id == job_id, JobPosting.platform == key)
            )
        ).scalar_one_or_none()
        reference = result.external_id or result.url
        if existing:
            existing.status = result.status.value
            existing.external_id = reference
            existing.posted_at = cast("Any", datetime.now())
        else:
            session.add(
                JobPosting(
                    job_requirement_id=job_id,
                    platform=key,
                    status=result.status.value,
                    external_id=reference,
                    posted_at=cast("Any", datetime.now()),
                )
            )

    await _log_job_activity(session, job, current_user, "published", {"platforms": list(request.platforms)})
    await session.commit()

    live = sum(1 for r in results if r["ok"])
    return {"message": f"Distributed to {live} of {len(results)} portals.", "results": results}


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
        work_mode=request.work_mode or "",
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
