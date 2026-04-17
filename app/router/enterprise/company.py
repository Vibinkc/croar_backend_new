from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import func, select

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.company import Company
from app.models.shared.constants import ModuleScope, PermissionAction
from app.models.shared.super_admin import SuperAdmin
from app.schemas.enterprise.company import CompanyCreate, CompanyResponse, CompanyUpdate

router = APIRouter(prefix="/company", tags=["Enterprise Company"])


@router.get("/stats")
async def get_global_stats(
    session: DBSessionDep,
    current_user: Annotated[Any, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.read))],
) -> dict:
    """Get global consultancy stats for the dashboard."""
    from sqlalchemy import or_

    from app.models.enterprise.job import JobRequirement

    is_super_admin = isinstance(current_user, SuperAdmin)
    company = getattr(current_user, "company", None)
    is_consultancy = getattr(company, "is_consultancy", False) if company else False

    # Base filter
    company_filter = Company.deleted_at.is_(None)
    job_filter = JobRequirement.deleted_at.is_(None)

    if not is_super_admin:
        if is_consultancy:
            company_filter = or_(
                Company.id == current_user.company_id, Company.parent_id == current_user.company_id
            )
            # Fetch all partner IDs
            partner_stmt = select(Company.id).where(
                Company.parent_id == current_user.company_id, Company.deleted_at.is_(None)
            )
            partner_ids = (await session.execute(partner_stmt)).scalars().all()
            job_filter = or_(
                JobRequirement.company_id == current_user.company_id,
                JobRequirement.company_id.in_(partner_ids),
            )
        else:
            company_filter = Company.id == current_user.company_id
            job_filter = JobRequirement.company_id == current_user.company_id

    # Total Managed Companies
    comp_stmt = select(func.count(Company.id)).where(company_filter)
    total_companies = (await session.execute(comp_stmt)).scalar() or 0

    # Total Active Jobs
    jobs_stmt = select(func.count(JobRequirement.id)).where(job_filter)
    total_jobs = (await session.execute(jobs_stmt)).scalar() or 0

    return {
        "total_companies": total_companies if (is_consultancy or is_super_admin) else 1,
        "total_jobs": total_jobs,
        "active_nodes": total_companies,
    }


@router.get("/", response_model=list[CompanyResponse])
async def list_companies(
    session: DBSessionDep,
    current_user: Annotated[Any, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.read))],
) -> list[CompanyResponse]:
    """List companies (restricted to current user's organization or partners)."""
    from sqlalchemy import or_

    is_super_admin = isinstance(current_user, SuperAdmin)
    company = getattr(current_user, "company", None)
    is_consultancy = getattr(company, "is_consultancy", False) if company else False

    stmt = select(Company).where(Company.deleted_at.is_(None))

    if not is_super_admin:
        if is_consultancy:
            # Show both the consultancy itself and its partners
            stmt = stmt.where(
                or_(Company.id == current_user.company_id, Company.parent_id == current_user.company_id)
            )
        else:
            stmt = stmt.where(Company.id == current_user.company_id)

    result = await session.execute(stmt)
    companies = result.scalars().all()
    return companies


@router.post("/", response_model=CompanyResponse)
async def create_company(
    session: DBSessionDep,
    current_user: Annotated[
        Any, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.create))
    ],
    company_data: CompanyCreate,
) -> CompanyResponse:
    """Create a new company to be managed by this tenant (Consultancy Partner)."""
    import re

    # Create a slug from the name
    slug = re.sub(r"[^a-zA-Z0-9]", "-", company_data.name.lower())
    slug = re.sub(r"-+", "-", slug).strip("-")

    is_super_admin = isinstance(current_user, SuperAdmin)
    company = getattr(current_user, "company", None)
    is_consultancy = getattr(company, "is_consultancy", False) if company else False

    # For super admin, we take parent_id from data if provided
    # For consultancy, we force parent_id to current_user.company_id
    parent_id = (
        company_data.parent_id if is_super_admin else (current_user.company_id if is_consultancy else None)
    )

    new_company = Company(
        slug=slug, parent_id=parent_id, **company_data.model_dump(exclude={"parent_id", "slug"})
    )
    session.add(new_company)
    await session.commit()
    await session.refresh(new_company)
    return new_company


@router.get("/{company_id}", response_model=CompanyResponse)
async def get_company(
    company_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[Any, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.read))],
) -> CompanyResponse:
    """Get a specific company profile."""
    from sqlalchemy import or_

    is_super_admin = isinstance(current_user, SuperAdmin)
    company = getattr(current_user, "company", None)
    is_consultancy = getattr(company, "is_consultancy", False) if company else False

    stmt = select(Company).where(Company.id == company_id, Company.deleted_at.is_(None))

    if not is_super_admin:
        if is_consultancy:
            stmt = stmt.where(
                or_(Company.id == current_user.company_id, Company.parent_id == current_user.company_id)
            )
        else:
            stmt = stmt.where(Company.id == current_user.company_id)

    result = await session.execute(stmt)
    company_res = result.scalar_one_or_none()

    if not company_res:
        raise HTTPException(status_code=404, detail="Company not found.")

    return company_res


@router.patch("/{company_id}", response_model=CompanyResponse)
async def update_company(
    company_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[
        Any, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.update))
    ],
    update_data: Annotated[CompanyUpdate, Body(...)],
) -> CompanyResponse:
    """Update a specific company profile."""
    from sqlalchemy import or_

    is_super_admin = isinstance(current_user, SuperAdmin)
    company_context = getattr(current_user, "company", None)
    is_consultancy = getattr(company_context, "is_consultancy", False) if company_context else False

    stmt = select(Company).where(Company.id == company_id, Company.deleted_at.is_(None))

    if not is_super_admin:
        if is_consultancy:
            stmt = stmt.where(
                or_(Company.id == current_user.company_id, Company.parent_id == current_user.company_id)
            )
        else:
            stmt = stmt.where(Company.id == current_user.company_id)

    result = await session.execute(stmt)
    company = result.scalar_one_or_none()

    if not company:
        raise HTTPException(status_code=404, detail="Company not found.")

    data = update_data.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(company, key, value)

    await session.commit()
    await session.refresh(company)
    return company


@router.delete("/{company_id}")
async def delete_company(
    company_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[
        Any, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.delete))
    ],
) -> dict:
    """Soft delete a company."""
    from datetime import datetime

    from sqlalchemy import or_

    is_super_admin = isinstance(current_user, SuperAdmin)
    company_context = getattr(current_user, "company", None)
    is_consultancy = getattr(company_context, "is_consultancy", False) if company_context else False

    stmt = select(Company).where(Company.id == company_id, Company.deleted_at.is_(None))

    if not is_super_admin:
        if is_consultancy:
            stmt = stmt.where(
                or_(Company.id == current_user.company_id, Company.parent_id == current_user.company_id)
            )
        else:
            stmt = stmt.where(Company.id == current_user.company_id)

    result = await session.execute(stmt)
    company = result.scalar_one_or_none()

    if not company:
        raise HTTPException(status_code=404, detail="Company not found.")

    company.deleted_at = datetime.now()
    await session.commit()
    return {"message": "Company deleted successfully"}


@router.get("/{company_id}/analytics")
async def get_company_analytics(
    company_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[Any, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.read))],
) -> dict:
    """Get hiring analytics for a specific company."""
    if company_id != current_user.company_id:
        raise HTTPException(status_code=403, detail="Access denied to this company's analytics.")
    from app.models.enterprise.candidate import CandidateApplication
    from app.models.enterprise.job import JobRequirement

    # Active Jobs
    jobs_stmt = select(JobRequirement).where(
        JobRequirement.company_id == company_id, JobRequirement.deleted_at.is_(None)
    )
    jobs = (await session.execute(jobs_stmt)).scalars().all()
    jobs_count = len(jobs)

    # Total Candidates & Avg Score
    cand_stmt = (
        select(
            CandidateApplication.id, CandidateApplication.ai_match_score, CandidateApplication.current_stage
        )
        .join(JobRequirement, CandidateApplication.job_requirement_id == JobRequirement.id)
        .where(JobRequirement.company_id == company_id)
    )
    cand_res = (await session.execute(cand_stmt)).all()

    cand_count = len(cand_res)
    avg_score = 0
    if cand_count > 0:
        scores = [r.ai_match_score for r in cand_res if r.ai_match_score is not None]
        if scores:
            avg_score = round(sum(scores) / len(scores), 1)

    # Stage distribution
    stage_counts = {}
    for r in cand_res:
        stage_counts[r.current_stage] = stage_counts.get(r.current_stage, 0) + 1

    # Sourcing Efficiency
    efficiency = 0
    if cand_count > 0:
        high_score_cands = [r for r in cand_res if (r.ai_match_score or 0) >= 70]
        efficiency = round((len(high_score_cands) / cand_count) * 100)

    # Recent Jobs
    recent_jobs = sorted(jobs, key=lambda x: x.created_at, reverse=True)[:5]

    return {
        "active_jobs": jobs_count,
        "total_candidates": cand_count,
        "avg_match_score": avg_score,
        "sourcing_efficiency": efficiency,
        "stage_distribution": stage_counts,
        "recent_jobs": [
            {"id": str(j.id), "title": j.title, "created_at": j.created_at.isoformat()} for j in recent_jobs
        ],
        "recent_activity": [],
    }
