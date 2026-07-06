from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.candidate import Candidate, CandidateApplication
from app.models.enterprise.interview import InterviewSchedule
from app.models.enterprise.job import JobRequirement
from app.models.shared.constants import ModuleScope, PermissionAction

router = APIRouter(prefix="/dashboard", tags=["Enterprise Dashboard"])

# Terminal application statuses — Hired (5), Rejected (6), Withdrawn (7) — which are
# not "to review" (application_statuses: 1 Applied, 2 Screening, 3 Interviewing,
# 4 Offered, 5 Hired, 6 Rejected, 7 Withdrawn).
TERMINAL_APPLICATION_STATUS_IDS = (5, 6, 7)


@router.get("/stats")
async def get_dashboard_stats(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.read))
    ],
) -> dict[str, Any]:
    """Get summarized stats for the enterprise dashboard."""
    company_id = getattr(current_user, "company_id", None)

    # 1. Active Jobs Count
    jobs_stmt = select(func.count(JobRequirement.id)).where(
        JobRequirement.company_id == company_id, JobRequirement.deleted_at.is_(None)
    )
    jobs_count = (await session.execute(jobs_stmt)).scalar() or 0

    # 2. Total Candidates Count
    candidates_stmt = select(func.count(Candidate.id)).where(
        Candidate.company_id == company_id, Candidate.deleted_at.is_(None)
    )
    candidates_count = (await session.execute(candidates_stmt)).scalar() or 0

    # 3. Total Applications Count
    apps_stmt = select(func.count(CandidateApplication.id)).where(
        CandidateApplication.company_id == company_id, CandidateApplication.deleted_at.is_(None)
    )
    apps_count = (await session.execute(apps_stmt)).scalar() or 0

    # 4. Interviews Scheduled
    #    Exclude interviews whose application was soft-deleted (e.g. when a job is deleted,
    #    delete_job soft-deletes the applications but leaves their InterviewSchedule rows).
    #    Without this filter those interviews keep inflating the count forever.
    interviews_stmt = (
        select(func.count(InterviewSchedule.id))
        .join(CandidateApplication, InterviewSchedule.application_id == CandidateApplication.id)
        .where(
            CandidateApplication.company_id == company_id,
            CandidateApplication.deleted_at.is_(None),
            InterviewSchedule.status == "SCHEDULED",
        )
    )
    interviews_count = (await session.execute(interviews_stmt)).scalar() or 0

    # 5. High Value Matches (AI Match Score >= 80) that are still ACTIONABLE.
    #    Surfaced to the user as "recommended candidates to review", so exclude the
    #    terminal statuses (see TERMINAL_APPLICATION_STATUS_IDS).
    high_value_stmt = select(func.count(CandidateApplication.id)).where(
        CandidateApplication.company_id == company_id,
        CandidateApplication.ai_match_score >= 80,
        CandidateApplication.status_id.notin_(TERMINAL_APPLICATION_STATUS_IDS),
        CandidateApplication.deleted_at.is_(None),
    )
    high_value_count = (await session.execute(high_value_stmt)).scalar() or 0

    # `first_name` may be absent (SuperAdmin) OR present-but-NULL — `getattr` only covers the
    # first case, so `or` also handles the NULL/empty value and keeps the greeting non-blank.
    first_name = getattr(current_user, "first_name", None) or "Recruiter"

    return {
        "active_jobs": jobs_count,
        "total_candidates": candidates_count,
        "total_applications": apps_count,
        "interviews_scheduled": interviews_count,
        "agent_name": first_name,
        "high_value_matches": high_value_count,
    }
