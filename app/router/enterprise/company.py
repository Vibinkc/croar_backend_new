from fastapi import APIRouter, Depends, HTTPException, Body
from typing import Annotated, Optional, List
from uuid import UUID
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import DBSessionDep, get_current_agent
from app.models.enterprise.user_role import EnterpriseUser as HiringAgent
from app.models.enterprise.company import Company
from app.schemas.enterprise.company import CompanyUpdate, CompanyResponse, CompanyCreate

router = APIRouter(prefix="/company", tags=["Enterprise Company"])

async def get_enterprise_agent(
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
) -> HiringAgent:
    return current_agent

@router.get("/stats")
async def get_global_stats(
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_enterprise_agent)]
):
    """Get global consultancy stats for the dashboard."""
    from app.models.enterprise.job import JobRequirement

    # Total Managed Companies
    comp_stmt = select(func.count(Company.id)).where(Company.deleted_at == None)
    total_companies = (await session.execute(comp_stmt)).scalar() or 0

    # Total Active Jobs
    jobs_stmt = select(func.count(JobRequirement.id)).where(JobRequirement.deleted_at == None)
    total_jobs = (await session.execute(jobs_stmt)).scalar() or 0

    return {
        "total_companies": total_companies,
        "total_jobs": total_jobs,
        "active_nodes": total_companies 
    }

@router.get("/", response_model=List[CompanyResponse])
async def list_companies(
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_enterprise_agent)]
):
    """List all companies managed by the consultancy."""
    stmt = select(Company).where(Company.deleted_at == None)
    result = await session.execute(stmt)
    companies = result.scalars().all()
    return companies

@router.post("/", response_model=CompanyResponse)
async def create_company(
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_enterprise_agent)],
    company_data: CompanyCreate
):
    """Create a new company to be managed by this tenant."""
    import re
    # Create a slug from the name
    slug = re.sub(r'[^a-zA-Z0-9]', '-', company_data.name.lower())
    slug = re.sub(r'-+', '-', slug).strip('-')
    
    new_company = Company(
        slug=slug,
        **company_data.model_dump()
    )
    session.add(new_company)
    await session.commit()
    await session.refresh(new_company)
    return new_company

@router.get("/{company_id}", response_model=CompanyResponse)
async def get_company(
    company_id: UUID,
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_enterprise_agent)]
):
    """Get a specific company profile."""
    stmt = select(Company).where(
        Company.id == company_id, 
        Company.deleted_at == None
    )
    result = await session.execute(stmt)
    company = result.scalar_one_or_none()
    
    if not company:
        raise HTTPException(status_code=404, detail="Company not found.")
    
    return company

@router.patch("/{company_id}", response_model=CompanyResponse)
async def update_company(
    company_id: UUID,
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_enterprise_agent)],
    update_data: CompanyUpdate = Body(...)
):
    """Update a specific company profile."""
    stmt = select(Company).where(
        Company.id == company_id, 
        Company.deleted_at == None
    )
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
    current_agent: Annotated[HiringAgent, Depends(get_enterprise_agent)]
):
    """Soft delete a company."""
    from datetime import datetime
    stmt = select(Company).where(
        Company.id == company_id
    )
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
    current_agent: Annotated[HiringAgent, Depends(get_enterprise_agent)]
):
    """Get hiring analytics for a specific company."""
    from app.models.enterprise.job import JobRequirement
    from app.models.enterprise.candidate import CandidateApplication

    # Active Jobs
    jobs_stmt = select(JobRequirement).where(JobRequirement.company_id == company_id, JobRequirement.deleted_at == None)
    jobs = (await session.execute(jobs_stmt)).scalars().all()
    jobs_count = len(jobs)

    # Total Candidates & Avg Score
    cand_stmt = select(CandidateApplication.id, CandidateApplication.ai_match_score, CandidateApplication.current_stage).join(JobRequirement, CandidateApplication.job_requirement_id == JobRequirement.id).where(JobRequirement.company_id == company_id)
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
        "recent_jobs": [{"id": str(j.id), "title": j.title, "created_at": j.created_at.isoformat()} for j in recent_jobs],
        "recent_activity": [] 
    }
