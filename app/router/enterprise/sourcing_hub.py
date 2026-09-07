"""The Sourcing Hub: structured search, and the path from a found profile onto a job.

Modelled on Manatal's Sourcing Hub, which is the screen recruiters know: a filter rail on the
left, results on the right, and a profile that can be turned into a candidate and put on a job
in two clicks.

One honest difference sits underneath it. Manatal's hub queries a licensed database of ~700
million profiles bought from a data broker; theirs is a subscription plus a credit per search.
Croar has no such licence and buying one is not a UI decision, so the same screen runs on
Croar's own sourcing providers — a web search shaped by the filters. Fewer results, current
rather than warehoused, and no per-search credit.

The import path is deliberately two steps, matching theirs: a search result is not a candidate
until someone says so. A found profile is a stranger on the internet; a candidate is a record
in your database with a source, an owner and a history.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.candidate import Candidate, CandidateApplication
from app.models.shared.constants import ModuleScope, PermissionAction
from app.router.enterprise.jobs import _get_scoped_job

router = APIRouter(prefix="/sourcing/hub", tags=["Sourcing Hub"])


def compose_query(
    job_titles: list[str],
    skills: list[str],
    company: str | None,
    years_min: int | None,
    years_max: int | None,
    school: str | None,
    degree: str | None,
    major: str | None,
) -> str:
    """Turn the filter rail into the one query string the sourcing providers take.

    Manatal's filters map onto database columns because it owns the index. Croar's providers
    take a search phrase, so the filters are composed into one — which is why the rail is the
    same but the matching is fuzzier. Saying that plainly beats implying a structured search
    that is not happening.
    """
    parts: list[str] = []
    if job_titles:
        parts.append(" OR ".join(f'"{t.strip()}"' for t in job_titles if t.strip()))
    if skills:
        parts.append(" ".join(s.strip() for s in skills if s.strip()))
    if company:
        parts.append(f'at "{company.strip()}"')
    if years_min is not None or years_max is not None:
        lo = years_min if years_min is not None else 0
        parts.append(f"{lo}+ years experience" if years_max is None else f"{lo}-{years_max} years experience")
    for value in (degree, major, school):
        if value and value.strip():
            parts.append(value.strip())
    return " ".join(p for p in parts if p).strip()


@router.get("/search")
async def hub_search(
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
    job_titles: Annotated[list[str] | None, Query()] = None,
    skills: Annotated[list[str] | None, Query()] = None,
    location: str | None = None,
    radius: int | None = None,
    company: str | None = None,
    years_min: int | None = None,
    years_max: int | None = None,
    school: str | None = None,
    degree: str | None = None,
    major: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(15, ge=1, le=50),
) -> dict[str, Any]:
    """Search with the filter rail, and say which filters actually reached the provider."""
    query = compose_query(
        job_titles or [], skills or [], company, years_min, years_max, school, degree, major
    )
    if not query:
        raise HTTPException(status_code=422, detail="Add at least one filter before searching.")

    from app.core.settings import get_settings
    from app.router.enterprise.sourcing import _search_single_platform, backfill_contacts, sanitize_profiles
    from app.services.enterprise.sourcing.base import SourcingUnavailable

    # Sample mode short-circuits the provider entirely rather than standing in for it when it
    # fails. A fallback would hide the very outage the 503 below exists to report, and the
    # reviewer would never learn their key had run dry.
    if get_settings().use_sample_sourcing:
        from app.services.enterprise.sourcing.sample_data import sample_profiles

        return {
            "query": query,
            "unused_filters": ["radius"] if radius else [],
            "page": page,
            "page_size": page_size,
            # The hub reads this to label every row. Without it, invented people are
            # indistinguishable from sourced ones the moment someone takes a screenshot.
            "sample": True,
            "results": sample_profiles(query, location, page_size),
        }

    # strict: one provider IS this search. A swallowed failure would reach the recruiter as an
    # empty result list, which the hub then explains as filters that were too narrow — sending
    # them to loosen filters that were never the problem.
    try:
        raw = await _search_single_platform("claude", query, location, page, page_size, strict=True)
    except SourcingUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Sourcing is unavailable right now, so no results could be fetched. {exc.reason}",
        ) from exc

    profiles = sanitize_profiles(raw)
    profiles = await backfill_contacts(profiles)

    return {
        "query": query,
        "sample": False,
        # Radius has nowhere to go: a web search cannot be bounded by kilometres the way a
        # geocoded database can. Returned so the UI can say so rather than pretend it applied.
        "unused_filters": ["radius"] if radius else [],
        "page": page,
        "page_size": page_size,
        "results": [p.model_dump() if hasattr(p, "model_dump") else p for p in profiles],
    }


class ImportProfile(BaseModel):
    """A search result being turned into a candidate."""

    full_name: str
    email: str | None = None
    headline: str | None = None
    location: str | None = None
    company: str | None = None
    profile_url: str | None = None
    avatar_url: str | None = None
    skills: list[str] = []
    education: list[dict[str, str]] = []
    experience: list[dict[str, str]] = []
    ai_summary: str | None = None
    # Optional: create the candidate and put them on this job in one step.
    job_id: UUID | None = None
    # Optional: file them in a folder in the same step. Independent of job_id — a folder is
    # a bookmark, a job is a process, and a recruiter may well want one without the other.
    folder_id: UUID | None = None


@router.post("/import", status_code=201)
async def import_profile(
    body: ImportProfile,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.create))
    ],
) -> dict[str, Any]:
    """Create a candidate from a sourced profile, optionally onto a job.

    Matches on email within the company so importing the same person twice updates one record
    rather than making a second — the same rule the CV parser and the apply form follow. A
    profile with no email cannot be matched that way, so it is always a new record; that is a
    property of the data, not a bug, and the UI says which results have contact details.
    """
    company_id = getattr(current_user, "company_id", None)
    if not company_id:
        raise HTTPException(status_code=404, detail="No company on this account.")

    email = (body.email or "").strip().lower() or None
    candidate = None
    if email:
        candidate = (
            await session.execute(
                select(Candidate).where(
                    Candidate.email == email,
                    Candidate.company_id == company_id,
                    Candidate.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    created = False
    if candidate is None:
        candidate = Candidate(
            full_name=body.full_name.strip() or "Unnamed profile",
            email=email,
            company_id=company_id,
            skills=[str(s) for s in body.skills][:40],
            source_platform="Sourcing Hub",
            parsed_data={
                # What the search actually saw, kept so a recruiter can tell where a record came
                # from months later. Sourced profiles have no CV behind them to re-read.
                "headline": body.headline,
                "location": body.location,
                "company": body.company,
                "profile_url": body.profile_url,
                "avatar_url": body.avatar_url,
                "education": body.education,
                "experience": body.experience,
                "ai_summary": body.ai_summary,
            },
        )
        session.add(candidate)
        await session.flush()
        created = True
    else:
        # Fill gaps only; never overwrite what a person may have corrected by hand.
        if not candidate.skills and body.skills:
            candidate.skills = [str(s) for s in body.skills][:40]

    application_id = None
    already_on_job = False
    if body.job_id:
        job = await _get_scoped_job(session, current_user, body.job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        existing = (
            await session.execute(
                select(CandidateApplication).where(
                    CandidateApplication.candidate_id == candidate.id,
                    CandidateApplication.job_requirement_id == job.id,
                    CandidateApplication.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if existing:
            application_id = existing.id
            already_on_job = True
        else:
            application = CandidateApplication(
                candidate_id=candidate.id,
                job_requirement_id=job.id,
                status_id=1,
                current_stage=1,
                # Distinct from "Added manually": a sourced candidate never applied, which
                # changes how the pipeline's own reporting should read.
                source="Sourcing Hub",
                company_id=company_id,
                applied_at=func.now(),
            )
            session.add(application)
            await session.flush()
            application_id = application.id

    folder_added = False
    if body.folder_id:
        from app.models.enterprise.folder import CandidateFolder, CandidateFolderMember

        folder = (
            await session.execute(
                select(CandidateFolder).where(
                    CandidateFolder.id == body.folder_id, CandidateFolder.company_id == company_id
                )
            )
        ).scalar_one_or_none()
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")
        member = (
            await session.execute(
                select(CandidateFolderMember).where(
                    CandidateFolderMember.folder_id == folder.id,
                    CandidateFolderMember.candidate_id == candidate.id,
                )
            )
        ).scalar_one_or_none()
        if not member:
            session.add(
                CandidateFolderMember(
                    folder_id=folder.id, candidate_id=candidate.id, added_by=getattr(current_user, "id", None)
                )
            )
            folder_added = True

    await session.commit()
    return {
        "candidate_id": str(candidate.id),
        "folder_added": folder_added,
        "created_candidate": created,
        "application_id": str(application_id) if application_id else None,
        "already_on_job": already_on_job,
        # Adding someone does not tell them. The invite is a separate, deliberate act.
        "notified": False,
    }
