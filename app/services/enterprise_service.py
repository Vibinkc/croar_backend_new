from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enterprise import Company, JobPosting, JobRequirement


class EnterpriseService:
    @staticmethod
    async def get_companies(db: AsyncSession) -> list[Company]:
        result = await db.execute(select(Company).where(Company.deleted_at.is_(None)))
        return list(result.scalars().all())

    @staticmethod
    async def get_jobs(db: AsyncSession) -> list[JobRequirement]:
        result = await db.execute(select(JobRequirement).where(JobRequirement.deleted_at.is_(None)))
        return list(result.scalars().all())

    @staticmethod
    async def create_job_requirement(db: AsyncSession, data: dict[str, object]) -> JobRequirement:
        new_job = JobRequirement(**data)
        db.add(new_job)
        await db.commit()
        await db.refresh(new_job)
        return new_job

    @staticmethod
    async def publish_job(db: AsyncSession, job_id: UUID, platform: str) -> JobPosting:
        posting = JobPosting(job_requirement_id=job_id, platform=platform, status="PUBLISHED")
        db.add(posting)
        await db.commit()
        await db.refresh(posting)
        return posting
