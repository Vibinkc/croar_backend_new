"""Reports — recruitment analytics across every job, not one at a time.

Croar already reports per job (the Reports tab on a job). This is the company view Manatal puts
under Settings & Analytics: how hiring is going overall, where candidates come from, and where
the pipeline leaks.

One rule runs through the whole file: report only what the data actually knows. Every figure
here is computed from a real column, and where a metric would need a field that does not exist,
it is absent rather than estimated. That is why there is no cost-per-hire and no offer-accept
rate — nothing records an offer or a cost, and a plausible-looking number derived from neither
is worse than a gap, because someone will put it in a board deck.

Where a figure is thin, the response says so rather than leaving the reader to guess: counts
carry their denominator, and time-to-hire reports how many hires it was averaged over.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Integer, cast, func, select
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.candidate import Candidate, CandidateApplication
from app.models.enterprise.job import JobRequirement
from app.models.shared.constants import ModuleScope, PermissionAction

router = APIRouter(prefix="/reports", tags=["Reports"])

# Score bands, strongest first, matching the per-job Reports tab so the two screens agree.
BANDS = [("strong", 80, 101), ("good", 60, 80), ("fair", 40, 60), ("weak", 0, 40)]


def _since(days: int) -> datetime:
    """Window start, as a NAIVE UTC datetime.

    `applied_at` is TIMESTAMP WITHOUT TIME ZONE, so comparing it to an aware datetime makes
    asyncpg refuse the parameter outright ("can't subtract offset-naive and offset-aware").
    Stripping the tzinfo after computing in UTC keeps the arithmetic correct and the type
    compatible with the column.
    """
    return (datetime.now(UTC) - timedelta(days=days)).replace(tzinfo=None)


@router.get("")
async def recruitment_report(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
    days: int = Query(90, ge=7, le=730),
) -> dict[str, Any]:
    """Company-wide recruitment reporting over the last `days`.

    The window applies to applications and to the trend, not to the job list: a role opened last
    year that is still taking applicants belongs on this page.
    """
    company_id = getattr(current_user, "company_id", None)
    if not company_id:
        raise HTTPException(status_code=404, detail="No company on this account.")

    since = _since(days)
    app_scope = [CandidateApplication.company_id == company_id, CandidateApplication.deleted_at.is_(None)]
    windowed = [*app_scope, CandidateApplication.applied_at >= since]

    # ── headline counts ──────────────────────────────────────────────────────
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

    total_apps = (
        await session.execute(select(func.count(CandidateApplication.id)).where(*windowed))
    ).scalar_one()
    total_candidates = (
        await session.execute(
            select(func.count(Candidate.id)).where(
                Candidate.company_id == company_id, Candidate.deleted_at.is_(None)
            )
        )
    ).scalar_one()

    # ── pipeline funnel ──────────────────────────────────────────────────────
    # Stage is an integer on the application; the stage NAMES live per job, so this reports the
    # numeric stage rather than inventing a shared vocabulary the data does not have.
    stage_rows = (
        await session.execute(
            select(CandidateApplication.current_stage, func.count(CandidateApplication.id))
            .where(*windowed)
            .group_by(CandidateApplication.current_stage)
            .order_by(CandidateApplication.current_stage)
        )
    ).all()
    funnel = [{"stage": int(s or 1), "count": c} for s, c in stage_rows]
    max_stage = max((f["stage"] for f in funnel), default=0)

    # ── where candidates come from ───────────────────────────────────────────
    # One expression object, reused in SELECT and GROUP BY. Building the coalesce twice renders
    # two different bind parameters, so Postgres sees two unrelated expressions and rejects the
    # grouping — "source must appear in the GROUP BY clause" even though it visibly does.
    src_col = func.coalesce(CandidateApplication.source, "Unknown").label("src")
    source_rows = (
        await session.execute(
            select(
                src_col, func.count(CandidateApplication.id), func.avg(CandidateApplication.ai_match_score)
            )
            .where(*windowed)
            .group_by(src_col)
            .order_by(func.count(CandidateApplication.id).desc())
        )
    ).all()
    sources = [
        {
            "source": src,
            "applications": count,
            # Average quality per source is the number that decides where to spend next quarter,
            # and it is null-safe: a source whose applicants were never scored reports null
            # rather than zero, which would read as "this source sends bad people".
            "avg_match_score": round(float(avg), 1) if avg is not None else None,
        }
        for src, count, avg in source_rows
    ]

    # ── match-quality distribution ───────────────────────────────────────────
    scored_total = (
        await session.execute(
            select(func.count(CandidateApplication.id)).where(
                *windowed, CandidateApplication.ai_match_score.is_not(None)
            )
        )
    ).scalar_one()
    quality = []
    for key, lo, hi in BANDS:
        n = (
            await session.execute(
                select(func.count(CandidateApplication.id)).where(
                    *windowed,
                    CandidateApplication.ai_match_score >= lo,
                    CandidateApplication.ai_match_score < hi,
                )
            )
        ).scalar_one()
        quality.append({"band": key, "min": lo, "max": hi, "count": n})

    # ── applications over time ───────────────────────────────────────────────
    trend_rows = (
        await session.execute(
            select(
                func.date_trunc("week", CandidateApplication.applied_at).label("wk"),
                func.count(CandidateApplication.id),
            )
            .where(*windowed)
            .group_by("wk")
            .order_by("wk")
        )
    ).all()
    trend = [{"week": wk.date().isoformat() if wk else None, "count": c} for wk, c in trend_rows if wk]

    # ── per-job table ────────────────────────────────────────────────────────
    per_job_rows = (
        await session.execute(
            select(
                CandidateApplication.job_requirement_id,
                func.count(CandidateApplication.id),
                func.avg(CandidateApplication.ai_match_score),
                func.max(cast(CandidateApplication.current_stage, Integer)),
            )
            .where(*app_scope)
            .group_by(CandidateApplication.job_requirement_id)
        )
    ).all()
    by_job = {jid: (cnt, avg, furthest) for jid, cnt, avg, furthest in per_job_rows}
    job_table = []
    for j in jobs:
        cnt, avg, furthest = by_job.get(j.id, (0, None, None))
        job_table.append(
            {
                "job_id": str(j.id),
                "title": j.title,
                "department": j.department,
                "status": getattr(j.status, "name", None),
                "open": j.accepting_applications,
                "headcount": j.headcount,
                "applications": cnt,
                "avg_match_score": round(float(avg), 1) if avg is not None else None,
                "furthest_stage": int(furthest) if furthest is not None else None,
                "created_at": j.created_at.isoformat() if getattr(j, "created_at", None) else None,
            }
        )
    job_table.sort(key=lambda r: r["applications"], reverse=True)

    return {
        "window_days": days,
        "generated_at": datetime.now(UTC).isoformat(),
        "totals": {
            "jobs": len(jobs),
            "open_jobs": len(open_jobs),
            "candidates": total_candidates,
            "applications": total_apps,
        },
        "funnel": funnel,
        "max_stage": max_stage,
        "sources": sources,
        "quality": quality,
        # Carried so the UI can say "of 12 scored" instead of implying the bands cover everyone.
        "scored_applications": scored_total,
        "trend": trend,
        "jobs": job_table,
        # Named explicitly rather than silently omitted, so nobody assumes these were computed
        # and came out as zero.
        "not_reported": ["time_to_hire", "offer_acceptance", "cost_per_hire"],
    }
