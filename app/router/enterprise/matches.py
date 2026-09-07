"""Matches — for each open job, the people already in your database who fit it.

Manatal's Recruitment Center opens on this screen, and the reason is a real one: most companies
have already met the person they are about to spend three weeks sourcing. Someone applied for a
different role last quarter, was strong, and lost. Nothing surfaces them again, so they are
re-found from scratch or not at all.

The direction matters. Croar already ranked JOBS for a candidate (the Candidate Bank's "invite
to a role" picker). This is the inverse and the more useful one day to day: given a job you are
trying to fill, who do you already have?

Scoring reuses `skill_match.overlap`, which is deterministic set overlap on skills — cheap, and
honest about what it knows. There is no LLM call here on purpose: a recommendation screen that
costs credits per view is a screen people stop opening, and the ranking it produces would not
be reproducible from one look to the next.

Candidates already on the job are excluded, because a "match" you have already actioned is
noise. Everything else is offered with its score and, deliberately, with the skills it is
MISSING — a bare percentage invites trust it has not earned, where "8 of 10, no Kubernetes"
lets a recruiter make the call themselves.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.candidate import Candidate, CandidateApplication
from app.models.enterprise.job import JobRequirement
from app.models.shared.constants import ModuleScope, PermissionAction
from app.services.enterprise.skill_match import overlap

router = APIRouter(prefix="/matches", tags=["Matches"])

# Below this, a "match" is mostly noise: one shared skill out of ten is not a recommendation.
# Exposed as a query parameter so a company with sparse skill data can widen it.
DEFAULT_MIN_SCORE = 20.0


def _experience_fit(candidate: Candidate, job: JobRequirement) -> float | None:
    """How well the candidate's recorded experience sits inside the job's band, 0-100.

    Returns None rather than a number when either side is unrecorded. A missing value shown as
    0 would rank an unknown candidate below a genuinely unsuitable one, which is worse than
    admitting the data is not there.
    """
    years = candidate.total_experience
    if years is None or (job.experience_min is None and job.experience_max is None):
        return None
    lo = job.experience_min if job.experience_min is not None else 0
    hi = job.experience_max if job.experience_max is not None else max(lo, years)
    if lo <= years <= hi:
        return 100.0
    # Outside the band, fall off by how far — being two years short is not the same as ten.
    distance = (lo - years) if years < lo else (years - hi)
    return max(0.0, round(100.0 - (distance * 20.0), 1))


def _score(skill_pct: float, exp_fit: float | None) -> float:
    """Blend skills with experience, weighted to skills.

    Experience is a band and a soft one; skills are the concrete requirement. When experience is
    unknown the score is the skill score alone rather than a penalty, so a candidate is never
    ranked down for a field nobody filled in.
    """
    if exp_fit is None:
        return round(skill_pct, 1)
    return round(skill_pct * 0.75 + exp_fit * 0.25, 1)


@router.get("")
async def list_matches(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
    job_id: UUID | None = None,
    min_score: float = Query(DEFAULT_MIN_SCORE, ge=0, le=100),
    per_job: int = Query(10, ge=1, le=50),
    limit_jobs: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """Open jobs, each with the best-fitting candidates you already have.

    Pass `job_id` to focus one job. Jobs with no qualifying match are still returned, with an
    empty list: "we looked and found nobody" is information, and hiding the job would make the
    screen look like the job did not exist.
    """
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

    # Only jobs that can actually take someone. Matching against a closed role produces
    # recommendations nobody can act on.
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

    # One query for every existing application, rather than one per job.
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
                    # The gap is the point: a bare percentage invites trust it has not earned.
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
                # Said plainly so the screen can explain an empty list rather than looking broken.
                "has_required_skills": bool(required),
                "match_count": len(rows),
                "matches": rows[:per_job],
            }
        )

    out.sort(key=lambda j: j["matches"][0]["score"] if j["matches"] else -1, reverse=True)
    return {"min_score": min_score, "candidate_pool": len(candidates), "jobs": out}


@router.get("/summary")
async def matches_summary(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
) -> dict[str, Any]:
    """Counts for the page header, and the one diagnostic that explains an empty screen.

    Matching is skill-overlap, so it produces nothing when nobody has skills recorded. Without
    saying so, a company that has never parsed a CV sees a blank page and concludes the feature
    is broken rather than that the data is missing.
    """
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
