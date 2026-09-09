"""Activities — scheduled calls, meetings and interviews.

Manatal's Activities screen: a list and a calendar of appointments a recruiter plans, with
Title, Type, Related To, Date, Time, Duration and Assignees.

Two decisions worth stating:

`view` defaults to "upcoming", matching the heading their screen opens on. A calendar that
opens on everything ever scheduled is a worse default than one that opens on what is about to
happen, and the filter badge on their toolbar exists precisely because the default is narrow.

`related_to` resolves to a candidate, a job, or the literal "Other". Returning a resolved label
rather than a bare id keeps the table one request instead of one per row, and the row is what
the recruiter actually reads.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.activity import ACTIVITY_TYPES, Activity
from app.models.enterprise.candidate import Candidate
from app.models.enterprise.job import JobRequirement
from app.models.enterprise.user_role import EnterpriseUser
from app.models.shared.constants import ModuleScope, PermissionAction

router = APIRouter(prefix="/activities", tags=["Activities"])

SORTABLE = {"title": Activity.title, "type": Activity.activity_type, "date": Activity.starts_at}


def _company(user: object) -> Any:
    cid = getattr(user, "company_id", None)
    if not cid:
        raise HTTPException(status_code=404, detail="No company on this account.")
    return cid


def _now() -> datetime:
    """Naive UTC — `starts_at` is TIMESTAMP WITHOUT TIME ZONE, and mixing the two makes asyncpg
    refuse the parameter outright."""
    return datetime.now(UTC).replace(tzinfo=None)


def _serialise(a: Activity) -> dict[str, Any]:
    related_label, related_type, related_id = "Other", "other", None
    if a.candidate is not None:
        related_label = a.candidate.full_name or "Candidate"
        related_type, related_id = "candidate", str(a.candidate_id)
    elif a.job is not None:
        related_label = a.job.title
        related_type, related_id = "job", str(a.job_requirement_id)
    return {
        "id": str(a.id),
        "title": a.title,
        "activity_type": a.activity_type,
        "notes": a.notes,
        "starts_at": a.starts_at.isoformat() if a.starts_at else None,
        "duration_minutes": a.duration_minutes,
        "related_label": related_label,
        "related_type": related_type,
        "related_id": related_id,
        "completed_at": a.completed_at.isoformat() if a.completed_at else None,
        "assignees": [
            {
                "id": str(u.id),
                "name": " ".join(filter(None, [u.first_name, u.last_name])) or u.email,
                "email": u.email,
            }
            for u in (a.assignees or [])
        ],
    }


class ActivityIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    activity_type: str = Field(default="other")
    notes: str | None = None
    starts_at: datetime
    duration_minutes: int = Field(default=30, ge=0, le=24 * 60)
    candidate_id: UUID | None = None
    job_requirement_id: UUID | None = None
    assignee_ids: list[UUID] = []


class ActivityPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    activity_type: str | None = None
    notes: str | None = None
    starts_at: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=0, le=24 * 60)
    candidate_id: UUID | None = None
    job_requirement_id: UUID | None = None
    assignee_ids: list[UUID] | None = None
    # Explicitly settable both ways, so the UI can tick and untick "done".
    completed: bool | None = None


async def _load(session: Any, company_id: Any, activity_id: UUID) -> Activity:
    a = (
        await session.execute(
            select(Activity)
            .options(selectinload(Activity.assignees))
            .where(Activity.id == activity_id, Activity.company_id == company_id)
        )
    ).scalar_one_or_none()
    if a is None:
        # 404 rather than 403 — an id belonging to another company simply does not exist here.
        raise HTTPException(status_code=404, detail="Activity not found")
    return a


async def _resolve_assignees(session: Any, company_id: Any, ids: list[UUID]) -> list[EnterpriseUser]:
    """Only users in this company. An id from elsewhere is dropped, not assigned."""
    if not ids:
        return []
    return list(
        (
            await session.execute(
                select(EnterpriseUser).where(
                    EnterpriseUser.id.in_(ids), EnterpriseUser.company_id == company_id
                )
            )
        )
        .scalars()
        .all()
    )


@router.get("")
async def list_activities(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
    view: Literal["upcoming", "past", "all", "completed"] = "upcoming",
    q: str | None = None,
    activity_type: str | None = None,
    assignee_id: UUID | None = None,
    candidate_id: UUID | None = None,
    job_id: UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    sort: str = Query("date", pattern="^(title|type|date)$"),
    direction: str = Query("asc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """The list view. Opens on what is coming up, which is what the screen is for."""
    cid = _company(current_user)
    where = [Activity.company_id == cid]

    now = _now()
    if view == "upcoming":
        where += [Activity.starts_at >= now, Activity.completed_at.is_(None)]
    elif view == "past":
        where += [Activity.starts_at < now, Activity.completed_at.is_(None)]
    elif view == "completed":
        where.append(Activity.completed_at.is_not(None))

    if q and q.strip():
        where.append(Activity.title.ilike(f"%{q.strip()}%"))
    if activity_type:
        where.append(Activity.activity_type == activity_type)
    if candidate_id:
        where.append(Activity.candidate_id == candidate_id)
    if job_id:
        where.append(Activity.job_requirement_id == job_id)
    if date_from:
        where.append(Activity.starts_at >= date_from.replace(tzinfo=None))
    if date_to:
        where.append(Activity.starts_at <= date_to.replace(tzinfo=None))
    if assignee_id:
        where.append(Activity.assignees.any(EnterpriseUser.id == assignee_id))

    total = (await session.execute(select(func.count(Activity.id)).where(and_(*where)))).scalar_one()

    col = SORTABLE[sort]
    rows = (
        (
            await session.execute(
                select(Activity)
                .options(selectinload(Activity.assignees))
                .where(and_(*where))
                .order_by(col.desc() if direction == "desc" else col.asc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .unique()
        .all()
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "view": view,
        "results": [_serialise(a) for a in rows],
    }


@router.get("/calendar")
async def calendar(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
    start: datetime,
    end: datetime,
    activity_type: str | None = None,
    assignee_id: UUID | None = None,
) -> dict[str, Any]:
    """Everything inside a window, for the day/week/month views.

    Unpaginated on purpose: a calendar that hides the 21st because it fell on page two is
    broken. The window is the bound, and a month of appointments is a small result.
    """
    cid = _company(current_user)
    where = [
        Activity.company_id == cid,
        Activity.starts_at >= start.replace(tzinfo=None),
        Activity.starts_at <= end.replace(tzinfo=None),
    ]
    if activity_type:
        where.append(Activity.activity_type == activity_type)
    if assignee_id:
        where.append(Activity.assignees.any(EnterpriseUser.id == assignee_id))

    rows = (
        (
            await session.execute(
                select(Activity)
                .options(selectinload(Activity.assignees))
                .where(and_(*where))
                .order_by(Activity.starts_at.asc())
            )
        )
        .scalars()
        .unique()
        .all()
    )
    return {"results": [_serialise(a) for a in rows]}


@router.get("/options")
async def options(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
) -> dict[str, Any]:
    """What the create form and the filter panel can offer, drawn from this company's own data."""
    cid = _company(current_user)
    users = (
        await session.execute(
            select(
                EnterpriseUser.id, EnterpriseUser.first_name, EnterpriseUser.last_name, EnterpriseUser.email
            )
            .where(EnterpriseUser.company_id == cid, EnterpriseUser.deleted_at.is_(None))
            .order_by(EnterpriseUser.first_name)
        )
    ).all()
    jobs = (
        await session.execute(
            select(JobRequirement.id, JobRequirement.title)
            .where(JobRequirement.company_id == cid, JobRequirement.deleted_at.is_(None))
            .order_by(JobRequirement.title)
            .limit(500)
        )
    ).all()
    candidates = (
        await session.execute(
            select(Candidate.id, Candidate.full_name, Candidate.email)
            .where(Candidate.company_id == cid, Candidate.deleted_at.is_(None))
            .order_by(Candidate.full_name)
            .limit(500)
        )
    ).all()
    return {
        "types": list(ACTIVITY_TYPES),
        "users": [
            {"id": str(i), "name": " ".join(filter(None, [first, last])) or e, "email": e}
            for i, first, last, e in users
        ],
        "jobs": [{"id": str(i), "title": t} for i, t in jobs],
        "candidates": [{"id": str(i), "name": n or e or "Unnamed"} for i, n, e in candidates],
    }


@router.post("", status_code=201)
async def create_activity(
    body: ActivityIn,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.create))
    ],
) -> dict[str, Any]:
    cid = _company(current_user)
    if body.activity_type not in ACTIVITY_TYPES:
        raise HTTPException(status_code=422, detail=f"Type must be one of: {', '.join(ACTIVITY_TYPES)}")
    # An activity relates to a candidate OR a job, never both — "Related To" is one column on
    # the row, and allowing two would make it ambiguous which one to print.
    if body.candidate_id and body.job_requirement_id:
        raise HTTPException(status_code=422, detail="Relate an activity to a candidate or a job, not both.")

    activity = Activity(
        title=body.title.strip(),
        activity_type=body.activity_type,
        notes=(body.notes or "").strip() or None,
        starts_at=body.starts_at.replace(tzinfo=None),
        duration_minutes=body.duration_minutes,
        candidate_id=body.candidate_id,
        job_requirement_id=body.job_requirement_id,
        company_id=cid,
        created_by=getattr(current_user, "id", None),
    )
    activity.assignees = await _resolve_assignees(session, cid, body.assignee_ids)
    session.add(activity)
    await session.commit()
    return _serialise(await _load(session, cid, activity.id))


@router.patch("/{activity_id}")
async def update_activity(
    activity_id: UUID,
    body: ActivityPatch,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.update))
    ],
) -> dict[str, Any]:
    cid = _company(current_user)
    a = await _load(session, cid, activity_id)

    if body.title is not None:
        a.title = body.title.strip()
    if body.activity_type is not None:
        if body.activity_type not in ACTIVITY_TYPES:
            raise HTTPException(status_code=422, detail=f"Type must be one of: {', '.join(ACTIVITY_TYPES)}")
        a.activity_type = body.activity_type
    if body.notes is not None:
        a.notes = body.notes.strip() or None
    if body.starts_at is not None:
        a.starts_at = body.starts_at.replace(tzinfo=None)
    if body.duration_minutes is not None:
        a.duration_minutes = body.duration_minutes
    if body.candidate_id is not None or body.job_requirement_id is not None:
        a.candidate_id = body.candidate_id
        a.job_requirement_id = body.job_requirement_id
    if body.assignee_ids is not None:
        a.assignees = await _resolve_assignees(session, cid, body.assignee_ids)
    if body.completed is not None:
        a.completed_at = _now() if body.completed else None

    await session.commit()
    return _serialise(await _load(session, cid, activity_id))


@router.delete("/{activity_id}", status_code=204)
async def delete_activity(
    activity_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.delete))
    ],
) -> None:
    cid = _company(current_user)
    a = await _load(session, cid, activity_id)
    await session.delete(a)
    await session.commit()


@router.get("/summary")
async def summary(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
) -> dict[str, Any]:
    """Counts for the view switcher, so each tab can carry its own number."""
    cid = _company(current_user)
    now = _now()
    scope = [Activity.company_id == cid]

    async def count(*extra: Any) -> int:
        return (
            await session.execute(select(func.count(Activity.id)).where(and_(*scope, *extra)))
        ).scalar_one()

    return {
        "upcoming": await count(Activity.starts_at >= now, Activity.completed_at.is_(None)),
        # Overdue is the number worth surfacing: a past activity nobody ticked off is either
        # forgotten or never happened, and both need a person to look.
        "past": await count(Activity.starts_at < now, Activity.completed_at.is_(None)),
        "completed": await count(Activity.completed_at.is_not(None)),
        "all": await count(),
        "today": await count(
            Activity.starts_at >= now.replace(hour=0, minute=0, second=0, microsecond=0),
            Activity.starts_at < now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1),
        ),
    }
