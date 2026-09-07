"""Reports — a catalogue of named reports, in Manatal's four categories.

Manatal's Reports is not one dashboard. It is a hub of four categories (Candidates, Jobs, Hiring
Performance, Leaderboard), each listing individually named reports you pick from — "Candidates
by Source", "Offer-to-Hire Ratio", "Average time to hire". That shape matters more than any
single chart: a recruiter opens Reports to answer one question, not to browse a wall of them.

Croar cannot support all ~35 of theirs, and the catalogue names the gaps rather than quietly
omitting them. Three fields they have and Croar does not:

  · application channel and sourcing channel as SEPARATE fields — Croar records one `source`
  · referrer — no field at all
  · a per-application owner — Croar has a job owner, not a recruiter per candidate, so every
    "by user" report and the whole Leaderboard category is unavailable

Listing those as unavailable-with-a-reason is the point. A catalogue containing only the
possible reports looks complete; one that names its gaps tells you what adding a `referrer`
column would actually buy.

A correction worth recording: the first version of this file asserted that Croar records no
offer and no hire, and left the funnel to the per-job stage integer as a result. That was
wrong. `application_statuses` is a real, maintained vocabulary — Applied, Screening,
Interviewing, Offered, Hired, Rejected, Withdrawn — and the live database has rows in every one
of them. The funnel, the stage ratios and time-to-hire are all computed from it, which is also
better than the stage integer, because these names are shared across every job.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Float, cast, func, select
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.candidate import ApplicationStatus, Candidate, CandidateApplication
from app.models.enterprise.job import JobRequirement
from app.models.shared.constants import ModuleScope, PermissionAction

router = APIRouter(prefix="/reports", tags=["Reports"])

# The canonical pipeline, in order. Shared across every job, unlike per-job stage names.
APPLIED, SCREENING, INTERVIEWING, OFFERED, HIRED = 1, 2, 3, 4, 5
FUNNEL_ORDER = [APPLIED, SCREENING, INTERVIEWING, OFFERED, HIRED]
STAGE_NAMES = {
    APPLIED: "Applied",
    SCREENING: "Screening",
    INTERVIEWING: "Interviewing",
    OFFERED: "Offered",
    HIRED: "Hired",
}

# Why a report is missing, so the catalogue can explain itself.
NO_CHANNELS = "Croar records one `source` per application, not separate application and sourcing channels."
NO_REFERRER = "Croar has no referrer field on a candidate."
NO_OWNER = "Croar has a job owner but no per-candidate recruiter, so results cannot be split by user."

CATALOGUE: list[dict[str, Any]] = [
    # ── Candidates ───────────────────────────────────────────────────────────
    {
        "id": "candidates_created",
        "category": "candidates",
        "section": "Overview",
        "name": "Candidates Created",
        "description": "Candidates added to your database, by month.",
    },
    {
        "id": "candidates_with_resumes",
        "category": "candidates",
        "section": "Overview",
        "name": "Resumes Added",
        "description": "Candidates with a CV attached, by month.",
    },
    {
        "id": "candidates_by_source",
        "category": "candidates",
        "section": "Source",
        "name": "Candidates by Source",
        "description": "Candidates sorted by where they came from.",
    },
    {
        "id": "candidates_by_source_over_time",
        "category": "candidates",
        "section": "Source over time",
        "name": "Candidates by Source over time",
        "description": "The same split, month by month.",
    },
    {
        "id": "candidates_by_application_channel",
        "category": "candidates",
        "section": "Source",
        "name": "Candidates by Application Channel",
        "unavailable": NO_CHANNELS,
    },
    {
        "id": "candidates_by_sourcing_channel",
        "category": "candidates",
        "section": "Source",
        "name": "Candidates by Sourcing Channel",
        "unavailable": NO_CHANNELS,
    },
    {
        "id": "candidates_by_referrer",
        "category": "candidates",
        "section": "Source",
        "name": "Candidates by Referrer",
        "unavailable": NO_REFERRER,
    },
    # ── Jobs ─────────────────────────────────────────────────────────────────
    {
        "id": "jobs_by_status",
        "category": "jobs",
        "section": "Jobs",
        "name": "Jobs by Status",
        "description": "Jobs sorted by their current status.",
    },
    {
        "id": "jobs_by_department",
        "category": "jobs",
        "section": "Jobs",
        "name": "Job table by department",
        "description": "Every job, grouped by department.",
    },
    # ── Hiring performance ───────────────────────────────────────────────────
    {
        "id": "hires_made",
        "category": "hiring",
        "section": "Overview",
        "name": "Hires Made",
        "description": "Applications that reached Hired, by month.",
    },
    {
        "id": "matches_created",
        "category": "hiring",
        "section": "Overview",
        "name": "Matches Created",
        "description": "Candidate-job pairings created, by month.",
    },
    {
        "id": "recruitment_funnel",
        "category": "hiring",
        "section": "Pipeline performance",
        "name": "Recruitment Funnel",
        "description": "Applications by pipeline status.",
    },
    {
        "id": "performance_by_job",
        "category": "hiring",
        "section": "Pipeline performance",
        "name": "Recruitment Performance by Job",
        "description": "Applications by job and status.",
    },
    {
        "id": "pipeline_ratios",
        "category": "hiring",
        "section": "Pipeline ratios",
        "name": "Pipeline Ratios",
        "description": "Conversion between each pair of pipeline stages.",
    },
    {
        "id": "time_to_hire",
        "category": "hiring",
        "section": "Velocity",
        "name": "Average time to hire",
        "description": "Days from application to Hired.",
    },
    {
        "id": "source_performance",
        "category": "hiring",
        "section": "Channel performance",
        "name": "Source Performance",
        "description": "Applications by source and how far they got.",
    },
    {
        "id": "interview_ratio_by_source",
        "category": "hiring",
        "section": "Interview ratios",
        "name": "Interview Ratio by Source",
        "description": "Share of each source reaching Interviewing.",
    },
    {
        "id": "referrer_performance",
        "category": "hiring",
        "section": "Channel performance",
        "name": "Referrer Performance",
        "unavailable": NO_REFERRER,
    },
    {
        "id": "performance_by_owner",
        "category": "hiring",
        "section": "Pipeline performance",
        "name": "Recruitment Performance by Match Owner",
        "unavailable": NO_OWNER,
    },
    # ── Leaderboard ──────────────────────────────────────────────────────────
    {
        "id": "candidates_by_user",
        "category": "leaderboard",
        "section": "Candidate performance",
        "name": "Candidates created by user",
        "unavailable": NO_OWNER,
    },
    {
        "id": "matches_by_user",
        "category": "leaderboard",
        "section": "Recruitment performance",
        "name": "Matches by user",
        "unavailable": NO_OWNER,
    },
    {
        "id": "hires_by_user",
        "category": "leaderboard",
        "section": "Recruitment performance",
        "name": "Hires by user",
        "unavailable": NO_OWNER,
    },
]

CATEGORIES = [
    {"id": "candidates", "name": "Candidates", "description": "Reports about the people in your database."},
    {"id": "jobs", "name": "Jobs", "description": "Reports about your open and closed roles."},
    {"id": "hiring", "name": "Hiring Performance", "description": "Pipeline, conversion and velocity."},
    {"id": "leaderboard", "name": "Leaderboard", "description": "Per-recruiter performance."},
]


def _since(days: int) -> datetime:
    """Naive UTC. These columns are TIMESTAMP WITHOUT TIME ZONE, and an aware datetime makes
    asyncpg refuse the parameter outright."""
    return (datetime.now(UTC) - timedelta(days=days)).replace(tzinfo=None)


def _company(user: object) -> Any:
    cid = getattr(user, "company_id", None)
    if not cid:
        raise HTTPException(status_code=404, detail="No company on this account.")
    return cid


@router.get("")
async def catalogue(
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
) -> dict[str, Any]:
    """Every report Croar offers, and every one it cannot, each with its reason."""
    return {
        "categories": CATEGORIES,
        "reports": [{**r, "available": "unavailable" not in r} for r in CATALOGUE],
    }


@router.get("/run/{report_id}")
async def run_report(
    report_id: str,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
    days: int = Query(180, ge=7, le=1095),
) -> dict[str, Any]:
    """Run one report, returning rows in a shape the page renders as bars, a series or a table."""
    meta = next((r for r in CATALOGUE if r["id"] == report_id), None)
    if not meta:
        raise HTTPException(status_code=404, detail="No such report.")
    if "unavailable" in meta:
        # 409 rather than 404: the report exists as a concept and is listed in the catalogue.
        # It cannot be produced from this schema, and the reason travels with the refusal.
        raise HTTPException(status_code=409, detail=meta["unavailable"])

    cid = _company(current_user)
    since = _since(days)
    apps = [CandidateApplication.company_id == cid, CandidateApplication.deleted_at.is_(None)]
    cands = [Candidate.company_id == cid, Candidate.deleted_at.is_(None)]

    async def fetch(stmt) -> list[Any]:
        return list((await session.execute(stmt)).all())

    out: dict[str, Any] = {"id": report_id, "name": meta["name"], "window_days": days}

    if report_id in ("candidates_created", "candidates_with_resumes"):
        month = func.date_trunc("month", Candidate.created_at).label("m")
        where = [*cands, Candidate.created_at >= since]
        if report_id == "candidates_with_resumes":
            where.append(Candidate.resume_file_path.is_not(None))
        r = await fetch(select(month, func.count(Candidate.id)).where(*where).group_by(month).order_by(month))
        out |= {"kind": "series", "rows": [{"label": m.date().isoformat(), "value": c} for m, c in r if m]}

    elif report_id == "candidates_by_source":
        src = func.coalesce(Candidate.source_platform, "Unknown").label("src")
        r = await fetch(
            select(src, func.count(Candidate.id))
            .where(*cands)
            .group_by(src)
            .order_by(func.count(Candidate.id).desc())
        )
        out |= {"kind": "bars", "rows": [{"label": s, "value": c} for s, c in r]}

    elif report_id == "candidates_by_source_over_time":
        src = func.coalesce(Candidate.source_platform, "Unknown").label("src")
        month = func.date_trunc("month", Candidate.created_at).label("m")
        r = await fetch(
            select(month, src, func.count(Candidate.id))
            .where(*cands, Candidate.created_at >= since)
            .group_by(month, src)
            .order_by(month)
        )
        out |= {
            "kind": "grouped",
            "rows": [{"period": m.date().isoformat(), "label": s, "value": c} for m, s, c in r if m],
        }

    elif report_id in ("jobs_by_status", "jobs_by_department"):
        jobs = list(
            (
                await session.execute(
                    select(JobRequirement)
                    .options(selectinload(JobRequirement.status))
                    .where(JobRequirement.company_id == cid, JobRequirement.deleted_at.is_(None))
                )
            )
            .scalars()
            .all()
        )
        if report_id == "jobs_by_status":
            by_status: dict[str, int] = {}
            for j in jobs:
                key = getattr(j.status, "name", None) or "Unknown"
                by_status[key] = by_status.get(key, 0) + 1
            out |= {
                "kind": "bars",
                "rows": [{"label": k, "value": v} for k, v in sorted(by_status.items(), key=lambda x: -x[1])],
            }
        else:
            out |= {
                "kind": "table",
                "columns": ["Department", "Job", "Status", "Openings"],
                "rows": [
                    {
                        "cells": [
                            j.department or "—",
                            j.title,
                            getattr(j.status, "name", None) or "—",
                            j.headcount,
                        ]
                    }
                    for j in sorted(jobs, key=lambda j: ((j.department or "~"), j.title))
                ],
            }

    elif report_id in ("hires_made", "matches_created"):
        month = func.date_trunc("month", CandidateApplication.applied_at).label("m")
        where = [*apps, CandidateApplication.applied_at >= since]
        if report_id == "hires_made":
            where.append(CandidateApplication.status_id == HIRED)
        r = await fetch(
            select(month, func.count(CandidateApplication.id)).where(*where).group_by(month).order_by(month)
        )
        out |= {"kind": "series", "rows": [{"label": m.date().isoformat(), "value": c} for m, c in r if m]}

    elif report_id == "recruitment_funnel":
        r = await fetch(
            select(ApplicationStatus.name, func.count(CandidateApplication.id))
            .join(CandidateApplication, CandidateApplication.status_id == ApplicationStatus.id)
            .where(*apps)
            .group_by(ApplicationStatus.name, ApplicationStatus.id)
            .order_by(ApplicationStatus.id)
        )
        out |= {"kind": "bars", "rows": [{"label": n, "value": c} for n, c in r]}

    elif report_id == "performance_by_job":
        r = await fetch(
            select(JobRequirement.title, ApplicationStatus.name, func.count(CandidateApplication.id))
            .join(CandidateApplication, CandidateApplication.job_requirement_id == JobRequirement.id)
            .outerjoin(ApplicationStatus, ApplicationStatus.id == CandidateApplication.status_id)
            .where(*apps)
            .group_by(JobRequirement.title, ApplicationStatus.name)
            .order_by(JobRequirement.title)
        )
        out |= {
            "kind": "grouped",
            "rows": [{"period": t, "label": s or "Unknown", "value": c} for t, s, c in r],
        }

    elif report_id == "pipeline_ratios":
        counts: dict[int, int] = dict(
            await fetch(
                select(CandidateApplication.status_id, func.count(CandidateApplication.id))
                .where(*apps)
                .group_by(CandidateApplication.status_id)
            )
        )
        # "Reached" is cumulative: someone Hired also passed through Interviewing. Counting each
        # status in isolation would report a 0% interview ratio for a company that hired
        # everyone it interviewed, which is the opposite of the truth.
        reached = {s: sum(counts.get(x, 0) for x in FUNNEL_ORDER[i:]) for i, s in enumerate(FUNNEL_ORDER)}
        pairs = [
            (APPLIED, SCREENING),
            (SCREENING, INTERVIEWING),
            (INTERVIEWING, OFFERED),
            (OFFERED, HIRED),
            (APPLIED, HIRED),
        ]
        out |= {
            "kind": "ratios",
            "rows": [
                {
                    "label": f"{STAGE_NAMES[a]} → {STAGE_NAMES[b]}",
                    "from": reached.get(a, 0),
                    "to": reached.get(b, 0),
                    "percent": round(100.0 * reached.get(b, 0) / reached[a], 1) if reached.get(a) else None,
                }
                for a, b in pairs
            ],
        }

    elif report_id == "time_to_hire":
        # `updated_at` is when the row last changed, which for a Hired application is the hire.
        # Approximate, and flagged as such so the page can label it rather than imply precision.
        r = await fetch(
            select(
                func.avg(
                    cast(
                        func.extract(
                            "epoch", CandidateApplication.updated_at - CandidateApplication.applied_at
                        ),
                        Float,
                    )
                ),
                func.count(CandidateApplication.id),
            ).where(
                *apps, CandidateApplication.status_id == HIRED, CandidateApplication.applied_at.is_not(None)
            )
        )
        secs, n = r[0] if r else (None, 0)
        out |= {
            "kind": "single",
            "unit": "days",
            "sample": n,
            "approximate": True,
            "value": round(secs / 86400.0, 1) if secs else None,
        }

    elif report_id == "source_performance":
        src = func.coalesce(CandidateApplication.source, "Unknown").label("src")
        r = await fetch(
            select(src, ApplicationStatus.name, func.count(CandidateApplication.id))
            .outerjoin(ApplicationStatus, ApplicationStatus.id == CandidateApplication.status_id)
            .where(*apps)
            .group_by(src, ApplicationStatus.name)
        )
        out |= {
            "kind": "grouped",
            "rows": [{"period": s, "label": st or "Unknown", "value": c} for s, st, c in r],
        }

    elif report_id == "interview_ratio_by_source":
        src = func.coalesce(CandidateApplication.source, "Unknown").label("src")
        totals = dict(
            await fetch(select(src, func.count(CandidateApplication.id)).where(*apps).group_by(src))
        )
        reached = dict(
            await fetch(
                select(src, func.count(CandidateApplication.id))
                .where(*apps, CandidateApplication.status_id.in_([INTERVIEWING, OFFERED, HIRED]))
                .group_by(src)
            )
        )
        out |= {
            "kind": "ratios",
            "rows": [
                {
                    "label": s,
                    "from": t,
                    "to": reached.get(s, 0),
                    "percent": round(100.0 * reached.get(s, 0) / t, 1) if t else None,
                }
                for s, t in sorted(totals.items(), key=lambda x: -x[1])
            ],
        }

    else:
        raise HTTPException(status_code=501, detail="That report is listed but not implemented yet.")

    return out
