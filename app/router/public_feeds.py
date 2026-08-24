"""Public, unauthenticated job-syndication feeds.

These are crawled by job boards, so they must be reachable without a token:

- ``GET /api/v1/jobs/feed/indeed.xml``          — Indeed Job Sync XML feed of published jobs.
- ``GET /api/v1/jobs/{job_id}/jobposting.jsonld`` — schema.org/JobPosting JSON-LD for one job
  (used by the public job page and by structured-data crawlers / Google for Jobs).

Only PUBLISHED postings are exposed. Optional ``?company_id=`` scopes a feed to one company.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep
from app.core.settings import settings
from app.models.enterprise.company import Company
from app.models.enterprise.job import JobPosting, JobRequirement
from app.services.enterprise.job_distribution import build_indeed_feed_xml, build_job_posting_jsonld

router = APIRouter(prefix="/jobs", tags=["Public Job Feeds"])

# JobPosting.status values that mean "actively distributed" (see job_distribution.DistributionStatus).
_LIVE_STATUSES = ("PUBLISHED", "LISTED", "QUEUED")


def _job_url(job_id: object) -> str:
    return f"{settings.frontend_url}/jobs/{job_id}"


def _apply_email(company: Company | None) -> str:
    return (
        (getattr(company, "contact_email", None) or "careers@croar.app") if company else "careers@croar.app"
    )


@router.get("/feed/indeed.xml")
async def indeed_feed(session: DBSessionDep, company_id: UUID | None = None) -> Response:
    """Indeed Job Sync XML feed of all currently-published jobs (optionally per company)."""
    stmt = (
        select(JobRequirement)
        .join(JobPosting, JobPosting.job_requirement_id == JobRequirement.id)
        .where(JobPosting.status.in_(_LIVE_STATUSES), JobRequirement.deleted_at.is_(None))
        .options(selectinload(JobRequirement.company))
        .distinct()
    )
    if company_id:
        stmt = stmt.where(JobRequirement.company_id == company_id)
    jobs = (await session.execute(stmt)).scalars().all()

    publisher = "Croar"
    entries = [(job, job.company, _job_url(job.id)) for job in jobs]
    # One apply email per feed; use the first company's contact if scoped.
    apply_email = _apply_email(jobs[0].company if jobs else None)
    xml = build_indeed_feed_xml(entries, publisher=publisher, apply_email=apply_email)
    return Response(content=xml, media_type="application/xml")


@router.get("/{job_id}/jobposting.jsonld")
async def jobposting_jsonld(job_id: UUID, session: DBSessionDep) -> Response:
    """schema.org/JobPosting JSON-LD for a single job (Google for Jobs / aggregators)."""
    import json

    stmt = (
        select(JobRequirement)
        .where(JobRequirement.id == job_id, JobRequirement.deleted_at.is_(None))
        .options(selectinload(JobRequirement.company))
    )
    job = (await session.execute(stmt)).scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    data = build_job_posting_jsonld(job, job.company, _job_url(job.id))
    return Response(content=json.dumps(data, ensure_ascii=False), media_type="application/ld+json")
