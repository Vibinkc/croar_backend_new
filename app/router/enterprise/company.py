import re
from datetime import datetime
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import func, or_, select

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.company import Company
from app.models.shared.constants import ModuleScope, PermissionAction
from app.models.shared.super_admin import SuperAdmin
from app.schemas.enterprise.company import CompanyCreate, CompanyResponse, CompanyUpdate

router = APIRouter(prefix="/company", tags=["Enterprise Company"])


@router.get("/stats")
async def get_global_stats(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.read))
    ],
) -> dict[str, Any]:
    """Get global consultancy stats for the dashboard."""
    from app.models.enterprise.job import JobRequirement

    is_super_admin = isinstance(current_user, SuperAdmin)
    company_context = getattr(current_user, "company", None)
    is_consultancy = getattr(company_context, "is_consultancy", False) if company_context else False
    company_id = getattr(current_user, "company_id", None)

    company_filter: Any = Company.deleted_at.is_(None)
    job_filter: Any = JobRequirement.deleted_at.is_(None)

    if not is_super_admin:
        if is_consultancy:
            company_filter = or_(Company.id == company_id, Company.parent_id == company_id)
            partner_stmt = select(Company.id).where(
                Company.parent_id == company_id, Company.deleted_at.is_(None)
            )
            partner_res = await session.execute(partner_stmt)
            partner_ids = partner_res.scalars().all()
            job_filter = or_(
                JobRequirement.company_id == company_id, JobRequirement.company_id.in_(partner_ids)
            )
        else:
            company_filter = Company.id == company_id
            job_filter = JobRequirement.company_id == company_id

    comp_stmt = select(func.count(Company.id)).where(company_filter)
    total_companies = (await session.execute(comp_stmt)).scalar() or 0

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
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.read))
    ],
) -> list[Company]:
    """List companies (restricted to current user's organization or partners)."""
    is_super_admin = isinstance(current_user, SuperAdmin)
    company_context = getattr(current_user, "company", None)
    is_consultancy = getattr(company_context, "is_consultancy", False) if company_context else False
    company_id = getattr(current_user, "company_id", None)

    stmt = select(Company).where(Company.deleted_at.is_(None))

    if not is_super_admin:
        if is_consultancy:
            stmt = stmt.where(or_(Company.id == company_id, Company.parent_id == company_id))
        else:
            stmt = stmt.where(Company.id == company_id)

    result = await session.execute(stmt)
    companies = result.scalars().all()
    return list(companies)


@router.post("/", response_model=CompanyResponse)
async def create_company(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.create))
    ],
    company_data: CompanyCreate,
) -> Company:
    """Create a new company to be managed by this tenant (Consultancy Partner)."""
    slug = re.sub(r"[^a-zA-Z0-9]", "-", company_data.name.lower())
    slug = re.sub(r"-+", "-", slug).strip("-")

    is_super_admin = isinstance(current_user, SuperAdmin)
    company_context = getattr(current_user, "company", None)
    is_consultancy = getattr(company_context, "is_consultancy", False) if company_context else False
    company_id = getattr(current_user, "company_id", None)

    parent_id = company_data.parent_id if is_super_admin else (company_id if is_consultancy else None)

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
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.read))
    ],
) -> Company:
    """Get a specific company profile."""
    is_super_admin = isinstance(current_user, SuperAdmin)
    company_context = getattr(current_user, "company", None)
    is_consultancy = getattr(company_context, "is_consultancy", False) if company_context else False
    my_company_id = getattr(current_user, "company_id", None)

    stmt = select(Company).where(Company.id == company_id, Company.deleted_at.is_(None))

    if not is_super_admin:
        if is_consultancy:
            stmt = stmt.where(or_(Company.id == my_company_id, Company.parent_id == my_company_id))
        else:
            stmt = stmt.where(Company.id == my_company_id)

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
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.update))
    ],
    update_data: Annotated[CompanyUpdate, Body(...)],
) -> Company:
    """Update a specific company profile."""
    is_super_admin = isinstance(current_user, SuperAdmin)
    company_context = getattr(current_user, "company", None)
    is_consultancy = getattr(company_context, "is_consultancy", False) if company_context else False
    my_company_id = getattr(current_user, "company_id", None)

    stmt = select(Company).where(Company.id == company_id, Company.deleted_at.is_(None))

    if not is_super_admin:
        if is_consultancy:
            stmt = stmt.where(or_(Company.id == my_company_id, Company.parent_id == my_company_id))
        else:
            stmt = stmt.where(Company.id == my_company_id)

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
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.delete))
    ],
) -> dict[str, str]:
    """Soft delete a company."""
    is_super_admin = isinstance(current_user, SuperAdmin)
    company_context = getattr(current_user, "company", None)
    is_consultancy = getattr(company_context, "is_consultancy", False) if company_context else False
    my_company_id = getattr(current_user, "company_id", None)

    stmt = select(Company).where(Company.id == company_id, Company.deleted_at.is_(None))

    if not is_super_admin:
        if is_consultancy:
            stmt = stmt.where(or_(Company.id == my_company_id, Company.parent_id == my_company_id))
        else:
            stmt = stmt.where(Company.id == my_company_id)

    result = await session.execute(stmt)
    company = result.scalar_one_or_none()

    if not company:
        raise HTTPException(status_code=404, detail="Company not found.")

    company.deleted_at = cast("Any", datetime.now())
    await session.commit()
    return {"message": "Company deleted successfully"}


@router.get("/{company_id}/analytics")
async def get_company_analytics(
    company_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.read))
    ],
) -> dict[str, Any]:
    """Get hiring analytics for a specific company."""
    my_company_id = getattr(current_user, "company_id", None)
    if company_id != my_company_id:
        raise HTTPException(status_code=403, detail="Access denied to this company's analytics.")

    from app.models.enterprise.candidate import CandidateApplication
    from app.models.enterprise.job import JobRequirement

    jobs_stmt = select(JobRequirement).where(
        JobRequirement.company_id == company_id, JobRequirement.deleted_at.is_(None)
    )
    jobs_res = await session.execute(jobs_stmt)
    jobs = list(jobs_res.scalars().all())
    jobs_count = len(jobs)

    cand_stmt = (
        select(
            CandidateApplication.id, CandidateApplication.ai_match_score, CandidateApplication.current_stage
        )
        .join(JobRequirement, CandidateApplication.job_requirement_id == JobRequirement.id)
        .where(JobRequirement.company_id == company_id)
    )
    cand_res = (await session.execute(cand_stmt)).all()

    cand_count = len(cand_res)
    avg_score = 0.0
    if cand_count > 0:
        scores = [float(r.ai_match_score) for r in cand_res if r.ai_match_score is not None]
        if scores:
            avg_score = round(sum(scores) / len(scores), 1)

    stage_counts: dict[int, int] = {}
    for r in cand_res:
        stage_id = int(cast("Any", r.current_stage))
        stage_counts[stage_id] = stage_counts.get(stage_id, 0) + 1

    efficiency = 0
    if cand_count > 0:
        high_score_cands = [r for r in cand_res if (r.ai_match_score or 0) >= 70]
        efficiency = round((len(high_score_cands) / cand_count) * 100)

    recent_jobs = sorted(jobs, key=lambda x: cast("datetime", x.created_at), reverse=True)[:5]

    return {
        "active_jobs": jobs_count,
        "total_candidates": cand_count,
        "avg_match_score": avg_score,
        "sourcing_efficiency": efficiency,
        "stage_distribution": stage_counts,
        "recent_jobs": [
            {"id": str(j.id), "title": j.title, "created_at": cast("datetime", j.created_at).isoformat()}
            for j in recent_jobs
        ],
        "recent_activity": [],
    }
