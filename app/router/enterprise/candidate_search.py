"""Advanced Search — structured search over the candidates you already have.

The counterpart to the Sourcing Hub, and the distinction matters. The hub asks "who else is out
there?" and answers from a live web search. This asks "who do we already know?" and answers
from your own database, so every filter here maps onto a real column and the matching is exact
rather than a phrase handed to a search engine.

That is why the two screens are not merged: identical-looking filter rails that mean different
things would be worse than two screens with one job each.

Registered ahead of the candidates router so `/candidates/search` is matched before
`/candidates/{candidate_id}` — otherwise "search" is parsed as a UUID and 422s.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.candidate import Candidate, CandidateApplication
from app.models.enterprise.folder import CandidateFolderMember
from app.models.shared.constants import ModuleScope, PermissionAction

router = APIRouter(prefix="/candidates/search", tags=["Candidate Search"])


@router.get("")
async def advanced_search(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
    name: str | None = None,
    email: str | None = None,
    skills: Annotated[list[str] | None, Query()] = None,
    match_all_skills: bool = False,
    location: str | None = None,
    company: str | None = None,
    source: str | None = None,
    folder_id: UUID | None = None,
    job_id: UUID | None = None,
    has_email: bool | None = None,
    years_min: int | None = None,
    years_max: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
) -> dict[str, Any]:
    """Search your own candidates on any combination of the filters below.

    Every filter is ANDed: adding one always narrows. Skills are the exception worth a switch —
    "React or Vue" and "React and Vue" are both things a recruiter means, so `match_all_skills`
    picks between them rather than guessing.
    """
    company_id = getattr(current_user, "company_id", None)
    if not company_id:
        raise HTTPException(status_code=404, detail="No company on this account.")

    conditions = [Candidate.company_id == company_id, Candidate.deleted_at.is_(None)]

    if name and name.strip():
        conditions.append(Candidate.full_name.ilike(f"%{name.strip()}%"))
    if email and email.strip():
        conditions.append(Candidate.email.ilike(f"%{email.strip()}%"))
    if source and source.strip():
        conditions.append(Candidate.source_platform.ilike(f"%{source.strip()}%"))

    if has_email is True:
        conditions.append(and_(Candidate.email.is_not(None), Candidate.email != ""))
    elif has_email is False:
        conditions.append(or_(Candidate.email.is_(None), Candidate.email == ""))

    cleaned_skills = [s.strip() for s in (skills or []) if s.strip()]
    if cleaned_skills:
        # ILIKE over the array rather than equality: stored skills are free text, so "React"
        # must still find "React.js" — an exact-match filter here would look broken.
        per_skill = [func.array_to_string(Candidate.skills, ",").ilike(f"%{s}%") for s in cleaned_skills]
        conditions.append(and_(*per_skill) if match_all_skills else or_(*per_skill))

    # Location and company live in parsed_data, which is where the CV parser and the Sourcing
    # Hub both put them; casting to text keeps this working whether the value is a string or
    # something the parser nested.
    if location and location.strip():
        conditions.append(Candidate.parsed_data["location"].astext.ilike(f"%{location.strip()}%"))
    if company and company.strip():
        conditions.append(Candidate.parsed_data["company"].astext.ilike(f"%{company.strip()}%"))

    if years_min is not None:
        conditions.append(Candidate.total_experience >= years_min)
    if years_max is not None:
        conditions.append(Candidate.total_experience <= years_max)

    if folder_id:
        conditions.append(
            Candidate.id.in_(
                select(CandidateFolderMember.candidate_id).where(CandidateFolderMember.folder_id == folder_id)
            )
        )
    if job_id:
        conditions.append(
            Candidate.id.in_(
                select(CandidateApplication.candidate_id).where(
                    CandidateApplication.job_requirement_id == job_id,
                    CandidateApplication.deleted_at.is_(None),
                )
            )
        )

    where = and_(*conditions)
    total = (await session.execute(select(func.count()).select_from(Candidate).where(where))).scalar_one()

    rows = (
        (
            await session.execute(
                select(Candidate)
                .where(where)
                .order_by(Candidate.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "results": [
            {
                "id": str(c.id),
                "full_name": c.full_name,
                "email": c.email,
                "phone": c.phone,
                "skills": c.skills or [],
                "source_platform": c.source_platform,
                "total_experience": c.total_experience,
                "headline": (c.parsed_data or {}).get("headline"),
                "location": (c.parsed_data or {}).get("location"),
                "company": (c.parsed_data or {}).get("company"),
            }
            for c in rows
        ],
    }


@router.get("/facets")
async def search_facets(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
) -> dict[str, Any]:
    """The distinct values worth offering as a dropdown, drawn from the data itself.

    A hard-coded source list goes stale the moment a new integration lands; asking the table
    means the filter can only ever offer values that will actually match something.
    """
    company_id = getattr(current_user, "company_id", None)
    if not company_id:
        raise HTTPException(status_code=404, detail="No company on this account.")

    sources = (
        (
            await session.execute(
                select(Candidate.source_platform)
                .where(
                    Candidate.company_id == company_id,
                    Candidate.deleted_at.is_(None),
                    Candidate.source_platform.is_not(None),
                )
                .distinct()
                .order_by(Candidate.source_platform)
            )
        )
        .scalars()
        .all()
    )
    return {"sources": [s for s in sources if s]}
