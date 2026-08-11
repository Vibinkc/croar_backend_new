from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.candidate import Candidate, CandidateApplication
from app.models.enterprise.company import Company
from app.models.enterprise.job import JobRequirement
from app.models.shared.constants import ModuleScope, PermissionAction
from app.schemas.enterprise.applications import CandidateBase

router = APIRouter(prefix="/candidates", tags=["Enterprise Candidates"])


async def _allowed_company_ids(session: DBSessionDep, current_user: object) -> list[Any]:
    """Own company + partner companies (for consultancies) — consistent with jobs/pipeline/onboarding."""
    company_id = getattr(current_user, "company_id", None)
    is_consultancy = getattr(getattr(current_user, "company", None), "is_consultancy", False)
    if not is_consultancy:
        return [company_id]
    partner_ids = (
        (await session.execute(select(Company.id).where(Company.parent_id == company_id))).scalars().all()
    )
    return [company_id, *partner_ids]


@router.get("/")
async def list_candidates(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
    q: str | None = None,
    job_id: UUID | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
) -> dict[str, Any]:
    """Paginated, server-side candidate search for the Candidate Bank.

    Filters: `q` (name / email / skill substring), `job_id` (applied to that job). Returns the page of
    candidates (each with their applied jobs), the total match count, and aggregate stat-card counts
    computed over the whole (unfiltered) candidate set — so the stats don't drift with the current page.
    Replaces the old approach of fetching every application and filtering in the browser.
    """
    allowed = await _allowed_company_ids(session, current_user)

    # --- Filtered search query ---
    base = select(Candidate).where(Candidate.company_id.in_(allowed), Candidate.deleted_at.is_(None))
    if job_id:
        base = base.where(
            Candidate.id.in_(
                select(CandidateApplication.candidate_id).where(
                    CandidateApplication.job_requirement_id == job_id,
                    CandidateApplication.deleted_at.is_(None),
                )
            )
        )
    if q and q.strip():
        like = f"%{q.strip()}%"
        base = base.where(
            or_(
                Candidate.full_name.ilike(like),
                Candidate.email.ilike(like),
                # Substring match against the skills array (array_to_string keeps it simple/portable).
                func.array_to_string(Candidate.skills, " ").ilike(like),
            )
        )

    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0

    rows = list(
        (
            await session.execute(
                base.order_by(Candidate.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
            )
        )
        .scalars()
        .all()
    )

    # Applied jobs for just this page of candidates (one round-trip, deduped per candidate).
    jobs_by_cand: dict[UUID, list[dict[str, str]]] = {}
    cand_ids = [c.id for c in rows]
    if cand_ids:
        app_rows = await session.execute(
            select(CandidateApplication.candidate_id, JobRequirement.id, JobRequirement.title)
            .join(JobRequirement, CandidateApplication.job_requirement_id == JobRequirement.id)
            .where(CandidateApplication.candidate_id.in_(cand_ids), CandidateApplication.deleted_at.is_(None))
        )
        for cid, jid, jtitle in app_rows.all():
            lst = jobs_by_cand.setdefault(cid, [])
            if not any(j["id"] == str(jid) for j in lst):
                lst.append({"id": str(jid), "title": jtitle})

    items = [
        {
            "id": str(c.id),
            "full_name": c.full_name,
            "email": c.email,
            "phone": c.phone,
            "skills": c.skills or [],
            "resume_file_path": c.resume_file_path,
            "created_at": cast("Any", c.created_at).isoformat() if c.created_at else None,
            "applied_jobs": jobs_by_cand.get(c.id, []),
        }
        for c in rows
    ]

    # --- Aggregate stats (over the whole candidate set, not the filtered page) ---
    scope = (Candidate.company_id.in_(allowed), Candidate.deleted_at.is_(None))
    total_all = (await session.execute(select(func.count(Candidate.id)).where(*scope))).scalar() or 0
    with_resume = (
        await session.execute(
            select(func.count(Candidate.id)).where(*scope, Candidate.resume_file_path.isnot(None))
        )
    ).scalar() or 0
    highly_skilled = (
        await session.execute(
            select(func.count(Candidate.id)).where(
                *scope, func.coalesce(func.cardinality(Candidate.skills), 0) > 5
            )
        )
    ).scalar() or 0
    multi_role_sub = (
        select(CandidateApplication.candidate_id)
        .where(CandidateApplication.company_id.in_(allowed), CandidateApplication.deleted_at.is_(None))
        .group_by(CandidateApplication.candidate_id)
        .having(func.count(func.distinct(CandidateApplication.job_requirement_id)) > 1)
    )
    multi_role = (
        await session.execute(select(func.count()).select_from(multi_role_sub.subquery()))
    ).scalar() or 0

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "stats": {
            "total": total_all,
            "multi_role": multi_role,
            "highly_skilled": highly_skilled,
            "with_resume": with_resume,
        },
    }


@router.get("/{candidate_id}", response_model=CandidateBase)
async def get_candidate(
    candidate_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
) -> Candidate:
    """Get candidate details by ID."""
    # Own company + partner companies (for consultancies) — consistent with jobs/candidates/onboarding
    # scoping, so a consultancy converting a partner candidate to an employee can still load it.
    allowed = await _allowed_company_ids(session, current_user)
    stmt = select(Candidate).where(Candidate.id == candidate_id, Candidate.company_id.in_(allowed))
    result = await session.execute(stmt)
    candidate = result.scalar_one_or_none()

    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    return candidate


@router.get("/{candidate_id}/matching-jobs")
async def get_matching_jobs(
    candidate_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
    limit: int = 20,
) -> dict[str, Any]:
    """The company's jobs ranked by how well they fit THIS candidate's skills — powers the Candidate
    Bank's "invite to a role" picker so you reach out about a role that actually suits them."""
    from app.services.enterprise.skill_match import overlap

    allowed = await _allowed_company_ids(session, current_user)
    candidate = (
        await session.execute(
            select(Candidate).where(Candidate.id == candidate_id, Candidate.company_id.in_(allowed))
        )
    ).scalar_one_or_none()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    jobs = list(
        (await session.execute(select(JobRequirement).where(JobRequirement.company_id.in_(allowed))))
        .scalars()
        .all()
    )
    applied_job_ids = {
        r[0]
        for r in (
            await session.execute(
                select(CandidateApplication.job_requirement_id).where(
                    CandidateApplication.candidate_id == candidate_id,
                    CandidateApplication.deleted_at.is_(None),
                )
            )
        ).all()
    }

    scored: list[tuple[int, float, JobRequirement, list[str]]] = []
    for j in jobs:
        cnt, matched, pct = overlap(candidate.skills, j.required_skills)
        scored.append((cnt, pct, j, matched))
    # Best match first; jobs with no shared skill still appear last, so you can invite anyway.
    scored.sort(key=lambda t: (-t[0], -t[1]))
    scored = scored[:limit]

    return {
        "candidate_id": str(candidate_id),
        "candidate_name": candidate.full_name,
        "jobs": [
            {
                "id": str(j.id),
                "title": j.title,
                "location": j.location,
                "required_skills": j.required_skills or [],
                "matched_skills": matched,
                "match_count": cnt,
                "match_pct": pct,
                "already_applied": j.id in applied_job_ids,
            }
            for cnt, pct, j, matched in scored
        ],
    }
