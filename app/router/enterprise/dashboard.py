from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.candidate import Candidate, CandidateApplication
from app.models.enterprise.interview import InterviewSchedule
from app.models.enterprise.job import JobRequirement
from app.models.shared.constants import ModuleScope, PermissionAction

router = APIRouter(prefix="/dashboard", tags=["Enterprise Dashboard"])

# Removed get_enterprise_agent helper as it's redundant with get_current_user


@router.get("/stats")
async def get_dashboard_stats(
    session: DBSessionDep,
    current_user: Annotated[Any, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.read))],
):
    """Get summarized stats for the enterprise dashboard."""

    # 1. Active Jobs Count
    jobs_stmt = select(func.count(JobRequirement.id)).where(
        JobRequirement.company_id == current_user.company_id, JobRequirement.deleted_at == None
    )
    jobs_count = (await session.execute(jobs_stmt)).scalar() or 0

    # 2. Total Candidates Count
    candidates_stmt = select(func.count(Candidate.id)).where(
        Candidate.company_id == current_user.company_id, Candidate.deleted_at == None
    )
    candidates_count = (await session.execute(candidates_stmt)).scalar() or 0

    # 3. Total Applications Count
    apps_stmt = select(func.count(CandidateApplication.id)).where(
        CandidateApplication.company_id == current_user.company_id, CandidateApplication.deleted_at == None
    )
    apps_count = (await session.execute(apps_stmt)).scalar() or 0

    # 4. Interviews Scheduled
    interviews_stmt = (
        select(func.count(InterviewSchedule.id))
        .join(CandidateApplication, InterviewSchedule.application_id == CandidateApplication.id)
        .where(
            CandidateApplication.company_id == current_user.company_id,
            InterviewSchedule.status == "SCHEDULED",
        )
    )
    interviews_count = (await session.execute(interviews_stmt)).scalar() or 0

    # 5. High Value Matches (AI Match Score >= 80)
    high_value_stmt = select(func.count(CandidateApplication.id)).where(
        CandidateApplication.company_id == current_user.company_id,
        CandidateApplication.ai_match_score >= 80,
        CandidateApplication.deleted_at == None,
    )
    high_value_count = (await session.execute(high_value_stmt)).scalar() or 0
    # Actually, user said 'real data', so let's stick to real. If 0, it's 0.
    # But wait, looking at the prompt, they might want at least SOME data if it's empty.
    # I'll stick to real data as requested.

    return {
        "active_jobs": jobs_count,
        "total_candidates": candidates_count,
        "total_applications": apps_count,
        "interviews_scheduled": interviews_count,
        "agent_name": current_user.first_name if current_user.first_name else "Recruiter",
        "high_value_matches": high_value_count,
    }
