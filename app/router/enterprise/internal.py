from typing import cast
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.enterprise.company import CompanyResponse
from app.schemas.enterprise.jobs import JobRequirementCreate, JobRequirementResponse, PublishJobRequest
from app.services.enterprise_service import EnterpriseService

router = APIRouter()


@router.get("/companies", response_model=list[CompanyResponse])
async def list_companies(db: AsyncSession = Depends(get_db)) -> list[object]:
    """List all companies."""
    return cast("list[object]", await EnterpriseService.get_companies(db))


@router.get("/jobs", response_model=list[JobRequirementResponse])
async def list_jobs(db: AsyncSession = Depends(get_db)) -> list[object]:
    """List all job requirements."""
    return cast("list[object]", await EnterpriseService.get_jobs(db))


@router.post("/jobs", response_model=JobRequirementResponse)
async def create_job(job_data: JobRequirementCreate, db: AsyncSession = Depends(get_db)) -> object:
    """Create a new job requirement."""
    return await EnterpriseService.create_job_requirement(db, job_data.model_dump())


@router.post("/jobs/{job_id}/publish")
async def publish_job(
    job_id: UUID, request: PublishJobRequest, db: AsyncSession = Depends(get_db)
) -> dict[str, object]:
    """Publish a job to multiple platforms."""
    results = []
    for platform in request.platforms:
        res = await EnterpriseService.publish_job(db, job_id, platform)
        results.append(res)
    return {"status": "success", "published_to": request.platforms}
