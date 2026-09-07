"""Matches — every candidate↔job pairing in the company, in one table.

This is what Manatal means by a "match": not a recommendation, but the association record itself.
Their screen is a flat, filterable, sortable list of Candidate Name / Position Name / Match Stage
/ Dropped across every job, and it earns its place because a pipeline board only ever shows one
job at a time. "Where is everyone, right now" has no other home.

Croar already stores exactly this as CandidateApplication; it simply had no cross-job view.

`Dropped` maps to status Rejected (6), which is what the job board's drop action sets — so the
column reports a real state rather than a derived guess.

The recommendation engine that briefly lived at this path now sits at /matches/recommendations.
It answers a different question ("who SHOULD be on this job?") and both are worth having, but
calling the recommender "Matches" made this list impossible to find and misnamed the feature
against the product it was modelled on.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.candidate import ApplicationStatus, Candidate, CandidateApplication
from app.models.enterprise.job import JobRequirement
from app.models.shared.constants import ModuleScope, PermissionAction
from app.services.enterprise.skill_match import overlap

router = APIRouter(prefix="/matches", tags=["Matches"])

# The job board's drop action sets this; the Dropped column reads it back.
STATUS_REJECTED = 6

SORTABLE = {
    "candidate": Candidate.full_name,
    "job": JobRequirement.title,
    "stage": CandidateApplication.current_stage,
    "applied": CandidateApplication.applied_at,
}


@router.get("")
async def list_matches(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
    q: str | None = None,
    job_id: UUID | None = None,
    status_id: int | None = None,
    dropped: bool | None = None,
    sort: str = Query("applied", pattern="^(candidate|job|stage|applied)$"),
    direction: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """Every pairing, newest first by default, with the filters the table header offers."""
    company_id = getattr(current_user, "company_id", None)
    if not company_id:
        raise HTTPException(status_code=404, detail="No company on this account.")

    base = (
        select(CandidateApplication, Candidate, JobRequirement, ApplicationStatus)
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .join(JobRequirement, JobRequirement.id == CandidateApplication.job_requirement_id)
        .outerjoin(ApplicationStatus, ApplicationStatus.id == CandidateApplication.status_id)
        .where(
            CandidateApplication.company_id == company_id,
            CandidateApplication.deleted_at.is_(None),
            Candidate.deleted_at.is_(None),
        )
    )
    if q and q.strip():
        like = f"%{q.strip()}%"
        base = base.where(or_(Candidate.full_name.ilike(like), JobRequirement.title.ilike(like)))
    if job_id:
        base = base.where(CandidateApplication.job_requirement_id == job_id)
    if status_id is not None:
        base = base.where(CandidateApplication.status_id == status_id)
    if dropped is True:
        base = base.where(CandidateApplication.status_id == STATUS_REJECTED)
    elif dropped is False:
        base = base.where(CandidateApplication.status_id != STATUS_REJECTED)

    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()

    col = SORTABLE[sort]
    ordered = base.order_by(col.desc() if direction == "desc" else col.asc())
    rows = (await session.execute(ordered.offset((page - 1) * page_size).limit(page_size))).all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "results": [
            {
                "application_id": str(app.id),
                "candidate_id": str(cand.id),
                "candidate_name": cand.full_name,
                "candidate_email": cand.email,
                "job_id": str(job.id),
                "job_title": job.title,
                "department": job.department,
                "stage": app.current_stage,
                "status_id": app.status_id,
                "status": getattr(status, "name", None),
                # Manatal's own column, and the reason it is here: a dropped pairing still
                # exists and still needs to be visible, or the count of "who did we consider"
                # silently shrinks over time.
                "dropped": app.status_id == STATUS_REJECTED,
                "source": app.source,
                "match_score": float(app.ai_match_score) if app.ai_match_score is not None else None,
                "applied_at": app.applied_at.isoformat() if app.applied_at else None,
            }
            for app, cand, job, status in rows
        ],
    }


@router.get("/filters")
async def match_filters(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
) -> dict[str, Any]:
    """The values the filter panel can offer, drawn from this company's own data.

    A hard-coded job list would go stale the moment someone opens a role, and offering a filter
    value that matches nothing is worse than not offering it.
    """
    company_id = getattr(current_user, "company_id", None)
    if not company_id:
        raise HTTPException(status_code=404, detail="No company on this account.")

    jobs = (
        await session.execute(
            select(JobRequirement.id, JobRequirement.title)
            .join(CandidateApplication, CandidateApplication.job_requirement_id == JobRequirement.id)
            .where(CandidateApplication.company_id == company_id, CandidateApplication.deleted_at.is_(None))
            .distinct()
            .order_by(JobRequirement.title)
        )
    ).all()
    statuses = (
        await session.execute(
            select(ApplicationStatus.id, ApplicationStatus.name).order_by(ApplicationStatus.id)
        )
    ).all()
    return {
        "jobs": [{"id": str(i), "title": t} for i, t in jobs],
        "statuses": [{"id": i, "name": n} for i, n in statuses],
    }


# ─────────────────────────── recommendations ────────────────────────────────
# A different question from the table above: not "who is on this job" but "who SHOULD be".
# Kept because it works and is useful, moved because naming it Matches misnamed it against the
# product it was modelled on and hid the list people actually came looking for.

DEFAULT_MIN_SCORE = 20.0


def _experience_fit(candidate: Candidate, job: JobRequirement) -> float | None:
    """How well recorded experience sits inside the job's band, 0-100, or None if unrecorded.

    None rather than 0: a missing value shown as zero would rank an unknown candidate below a
    genuinely unsuitable one, which is worse than admitting the data is not there.
    """
    years = candidate.total_experience
    if years is None or (job.experience_min is None and job.experience_max is None):
        return None
    lo = job.experience_min if job.experience_min is not None else 0
    hi = job.experience_max if job.experience_max is not None else max(lo, years)
    if lo <= years <= hi:
        return 100.0
    distance = (lo - years) if years < lo else (years - hi)
    return max(0.0, round(100.0 - (distance * 20.0), 1))


def _score(skill_pct: float, exp_fit: float | None) -> float:
    """Skills carry it; experience adjusts. Unknown experience is not a penalty."""
    if exp_fit is None:
        return round(skill_pct, 1)
    return round(skill_pct * 0.75 + exp_fit * 0.25, 1)


@router.get("/recommendations")
async def recommendations(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
    job_id: UUID | None = None,
    min_score: float = Query(DEFAULT_MIN_SCORE, ge=0, le=100),
    per_job: int = Query(10, ge=1, le=50),
    limit_jobs: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """Open jobs, each with the best-fitting candidates you already have but have not added."""
    company_id = getattr(current_user, "company_id", None)
    if not company_id:
        raise HTTPException(status_code=404, detail="No company on this account.")

    job_q = (
        select(JobRequirement)
        .options(selectinload(JobRequirement.status))
        .where(JobRequirement.company_id == company_id, JobRequirement.deleted_at.is_(None))
    )
    if job_id:
        job_q = job_q.where(JobRequirement.id == job_id)
    jobs = list((await session.execute(job_q)).scalars().all())
    if job_id and not jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job_id:
        jobs = [j for j in jobs if j.accepting_applications][:limit_jobs]

    candidates = list(
        (
            await session.execute(
                select(Candidate).where(Candidate.company_id == company_id, Candidate.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    )

    applied: dict[UUID, set[UUID]] = {}
    for cand_id, jid in (
        await session.execute(
            select(CandidateApplication.candidate_id, CandidateApplication.job_requirement_id).where(
                CandidateApplication.company_id == company_id, CandidateApplication.deleted_at.is_(None)
            )
        )
    ).all():
        applied.setdefault(jid, set()).add(cand_id)

    out: list[dict[str, Any]] = []
    for job in jobs:
        required = [s for s in (job.required_skills or []) if s and str(s).strip()]
        already = applied.get(job.id, set())
        rows: list[dict[str, Any]] = []
        for c in candidates:
            if c.id in already:
                continue
            count, matched, pct = overlap(c.skills, required)
            exp = _experience_fit(c, job)
            score = _score(pct, exp)
            if score < min_score:
                continue
            matched_lower = {m.lower() for m in matched}
            rows.append(
                {
                    "candidate_id": str(c.id),
                    "full_name": c.full_name,
                    "email": c.email,
                    "headline": (c.parsed_data or {}).get("headline"),
                    "location": (c.parsed_data or {}).get("location"),
                    "source_platform": c.source_platform,
                    "total_experience": c.total_experience,
                    "score": score,
                    "skill_percent": pct,
                    "experience_fit": exp,
                    "matched_skills": matched,
                    "missing_skills": [s for s in required if s.lower() not in matched_lower],
                    "matched_count": count,
                    "required_count": len(required),
                }
            )
        rows.sort(key=lambda r: r["score"], reverse=True)
        out.append(
            {
                "job_id": str(job.id),
                "title": job.title,
                "department": job.department,
                "location": job.location,
                "status": getattr(job.status, "name", None),
                "required_skills": required,
                "has_required_skills": bool(required),
                "match_count": len(rows),
                "matches": rows[:per_job],
            }
        )
    out.sort(key=lambda j: j["matches"][0]["score"] if j["matches"] else -1, reverse=True)
    return {"min_score": min_score, "candidate_pool": len(candidates), "jobs": out}


@router.get("/recommendations/summary")
async def recommendations_summary(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
) -> dict[str, Any]:
    """Counts for the header, and the diagnostics that explain an empty recommendation screen."""
    company_id = getattr(current_user, "company_id", None)
    if not company_id:
        raise HTTPException(status_code=404, detail="No company on this account.")

    candidates_total = (
        await session.execute(
            select(func.count(Candidate.id)).where(
                Candidate.company_id == company_id, Candidate.deleted_at.is_(None)
            )
        )
    ).scalar_one()
    candidates_with_skills = (
        await session.execute(
            select(func.count(Candidate.id)).where(
                Candidate.company_id == company_id,
                Candidate.deleted_at.is_(None),
                func.coalesce(func.array_length(Candidate.skills, 1), 0) > 0,
            )
        )
    ).scalar_one()
    jobs = list(
        (
            await session.execute(
                select(JobRequirement)
                .options(selectinload(JobRequirement.status))
                .where(JobRequirement.company_id == company_id, JobRequirement.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    open_jobs = [j for j in jobs if j.accepting_applications]
    return {
        "open_jobs": len(open_jobs),
        "jobs_with_required_skills": sum(1 for j in open_jobs if j.required_skills),
        "candidates_total": candidates_total,
        "candidates_with_skills": candidates_with_skills,
    }
