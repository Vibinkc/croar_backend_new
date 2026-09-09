"""Offboarding — the exit process Croar never had.

Modelled on Oorwin's, walked in their live product: a two-step "Create Offboarding Initiation
Request", where step one records the decision and step two is a checklist.

The checklist is what makes this a process rather than a status field, and it is **seeded from
real data**:

  * one task per asset the person is actually holding, read from the asset register — so
    "return the MacBook" exists because the register says they have it, not because somebody
    remembered to type it;
  * completing an asset task returns the asset for real, so the checklist and the register
    cannot end up disagreeing about whether the laptop came home;
  * one task to revoke their login, which is a real EnterpriseUser this module can deactivate;
  * the standard finance and HR items, which are reminders rather than actions.

Completion is gated: an offboarding cannot be marked complete while a required task is open.
That gate is the only reason to build this rather than add a "left" flag to the employee — it is
what stops someone walking out with a laptop and their access still live.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.asset import Asset, AssetAssignment
from app.models.enterprise.employee import Employee
from app.models.enterprise.offboarding import (
    OFFBOARDING_REASONS,
    OFFBOARDING_STATUSES,
    OFFBOARDING_TYPES,
    TASK_CATEGORIES,
    Offboarding,
    OffboardingTask,
)
from app.models.enterprise.user_role import EnterpriseUser
from app.models.shared.constants import ModuleScope, PermissionAction

router = APIRouter(prefix="/offboarding", tags=["Offboarding"])

# The items every exit needs regardless of who is leaving. Assets are generated on top of these
# from what the person actually holds. Required is set where skipping costs money or leaves a
# door open; the rest are reminders and do not block completion.
STANDARD_TASKS: list[dict[str, Any]] = [
    {
        "title": "Revoke system access",
        "category": "access",
        "required": True,
        "detail": "Disables their Croar login. Do this on the last working day, not before.",
    },
    {"title": "Collect ID and access card", "category": "access", "required": True},
    {
        "title": "Handover of work in progress",
        "category": "knowledge",
        "required": True,
        "detail": "Who picks up each open job, candidate and project.",
    },
    {"title": "Knowledge transfer session", "category": "knowledge", "required": False},
    {
        "title": "Final settlement calculated",
        "category": "finance",
        "required": True,
        "detail": "Salary to the last working day, unused leave, notice adjustment, any recovery.",
    },
    {"title": "Recover outstanding advances", "category": "finance", "required": False},
    {"title": "Exit interview", "category": "hr", "required": False},
    {"title": "Relieving letter issued", "category": "hr", "required": True},
    {"title": "Experience letter issued", "category": "hr", "required": False},
]


def _company(user: object) -> Any:
    cid = getattr(user, "company_id", None)
    if not cid:
        raise HTTPException(status_code=404, detail="No company on this account.")
    return cid


def _now() -> datetime:
    """Naive UTC — these columns are TIMESTAMP WITHOUT TIME ZONE."""
    return datetime.now(UTC).replace(tzinfo=None)


def _naive(d: datetime | None) -> datetime | None:
    return d.replace(tzinfo=None) if d and d.tzinfo else d


def _employee_name(e: Employee | None) -> str | None:
    if e is None:
        return None
    return " ".join(p for p in (e.first_name, e.last_name) if p).strip() or e.email


def _task_out(t: OffboardingTask) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "title": t.title,
        "detail": t.detail,
        "category": t.category,
        "required": t.required,
        "position": t.position,
        "asset_id": str(t.asset_id) if t.asset_id else None,
        "owner_id": str(t.owner_id) if t.owner_id else None,
        "due_on": t.due_on.isoformat() if t.due_on else None,
        "done": t.done,
        "done_at": t.done_at.isoformat() if t.done_at else None,
        "note": t.note,
    }


def _out(o: Offboarding) -> dict[str, Any]:
    tasks = list(o.tasks)
    done = sum(1 for t in tasks if t.done)
    blocking = [t for t in tasks if t.required and not t.done]
    return {
        "id": str(o.id),
        "employee_id": str(o.employee_id),
        "employee_name": _employee_name(o.employee),
        "offboarding_type": o.offboarding_type,
        "resignation_date": o.resignation_date.isoformat() if o.resignation_date else None,
        "last_working_day": o.last_working_day.isoformat() if o.last_working_day else None,
        "reason": o.reason,
        "reason_other": o.reason_other,
        "rehire_eligible": o.rehire_eligible,
        "comments": o.comments,
        "status": o.status,
        "requested_at": o.requested_at.isoformat() if o.requested_at else None,
        "decided_at": o.decided_at.isoformat() if o.decided_at else None,
        "decision_note": o.decision_note,
        "completed_at": o.completed_at.isoformat() if o.completed_at else None,
        "tasks": [_task_out(t) for t in tasks],
        "task_count": len(tasks),
        "tasks_done": done,
        # Named, not counted. "3 tasks left" tells you nothing you can act on; the titles do.
        "blocking": [t.title for t in blocking],
        "can_complete": not blocking and o.status in ("approved", "in_progress"),
    }


async def _load(session: DBSessionDep, cid: Any, oid: uuid.UUID) -> Offboarding:
    o = (
        await session.execute(
            select(Offboarding).where(
                Offboarding.id == oid, Offboarding.company_id == cid, Offboarding.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if o is None:
        raise HTTPException(status_code=404, detail="Offboarding not found")
    return o


class OffboardingIn(BaseModel):
    employee_id: uuid.UUID
    offboarding_type: Literal["resignation", "termination"]
    resignation_date: datetime | None = None
    last_working_day: datetime | None = None
    reason: Literal[
        "better_opportunity",
        "compensation",
        "relocation",
        "personal",
        "health",
        "performance",
        "misconduct",
        "redundancy",
        "end_of_contract",
        "retirement",
        "other",
    ]
    reason_other: str | None = None
    rehire_eligible: bool | None = None
    comments: str | None = None


@router.get("/options")
async def options(
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.read))],
) -> dict[str, Any]:
    """The enums the form needs, in one call."""
    return {
        "types": list(OFFBOARDING_TYPES),
        "reasons": list(OFFBOARDING_REASONS),
        "statuses": list(OFFBOARDING_STATUSES),
        "task_categories": list(TASK_CATEGORIES),
    }


@router.get("")
async def list_offboardings(
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.read))],
    q: str | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
) -> dict[str, Any]:
    cid = _company(current_user)
    where = [Offboarding.company_id == cid, Offboarding.deleted_at.is_(None)]
    if status:
        where.append(Offboarding.status == status)
    if q and q.strip():
        like = f"%{q.strip()}%"
        where.append(
            Offboarding.employee_id.in_(
                select(Employee.id).where(
                    Employee.company_id == cid,
                    or_(
                        Employee.first_name.ilike(like),
                        Employee.last_name.ilike(like),
                        Employee.email.ilike(like),
                    ),
                )
            )
        )

    total = (await session.execute(select(func.count()).select_from(Offboarding).where(*where))).scalar_one()
    rows = (
        (
            await session.execute(
                select(Offboarding)
                .where(*where)
                .order_by(Offboarding.requested_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .unique()
        .all()
    )
    return {"total": total, "page": page, "page_size": page_size, "results": [_out(o) for o in rows]}


@router.post("", status_code=201)
async def create_offboarding(
    body: OffboardingIn,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.create))
    ],
) -> dict[str, Any]:
    """Raise the request, and build the checklist from what this person actually holds."""
    cid = _company(current_user)

    if body.reason == "other" and not (body.reason_other or "").strip():
        raise HTTPException(status_code=422, detail="Say what the reason is.")
    if body.resignation_date and body.last_working_day and body.last_working_day < body.resignation_date:
        # Notice runs from the resignation to the last day; the other way round is a typo, and
        # a negative notice period silently corrupts a final settlement.
        raise HTTPException(
            status_code=422, detail="The last working day cannot be before the resignation date."
        )

    emp = (
        await session.execute(
            select(Employee).where(
                Employee.id == body.employee_id, Employee.company_id == cid, Employee.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    open_one = (
        await session.execute(
            select(Offboarding).where(
                Offboarding.employee_id == emp.id,
                Offboarding.company_id == cid,
                Offboarding.deleted_at.is_(None),
                Offboarding.status.in_(("requested", "approved", "in_progress")),
            )
        )
    ).scalar_one_or_none()
    if open_one:
        raise HTTPException(status_code=409, detail="This person already has an offboarding in progress.")

    o = Offboarding(
        employee_id=emp.id,
        offboarding_type=body.offboarding_type,
        resignation_date=_naive(body.resignation_date),
        last_working_day=_naive(body.last_working_day),
        reason=body.reason,
        reason_other=(body.reason_other or "").strip() or None,
        rehire_eligible=body.rehire_eligible,
        comments=(body.comments or "").strip() or None,
        status="requested",
        requested_by=getattr(current_user, "id", None),
        requested_at=_now(),
        company_id=cid,
    )
    session.add(o)
    await session.flush()

    # ── the checklist, seeded from real state ───────────────────────────────
    due = _naive(body.last_working_day)
    pos = 0

    held = (
        (
            await session.execute(
                select(AssetAssignment)
                .where(
                    AssetAssignment.company_id == cid,
                    AssetAssignment.employee_id == emp.id,
                    AssetAssignment.returned_at.is_(None),
                )
                .order_by(AssetAssignment.assigned_at)
            )
        )
        .scalars()
        .unique()
        .all()
    )
    for a in held:
        session.add(
            OffboardingTask(
                offboarding_id=o.id,
                title=f"Return {a.asset.name} ({a.asset.asset_tag})",
                detail=f"Held since {a.assigned_at.date().isoformat()}." if a.assigned_at else None,
                category="assets",
                position=pos,
                asset_id=a.asset_id,
                # Always required: an unreturned asset is the one exit failure with a price on it.
                required=True,
                due_on=due,
                company_id=cid,
            )
        )
        pos += 1

    for spec in STANDARD_TASKS:
        session.add(
            OffboardingTask(
                offboarding_id=o.id,
                title=spec["title"],
                detail=spec.get("detail"),
                category=spec["category"],
                position=pos,
                required=spec["required"],
                due_on=due,
                company_id=cid,
            )
        )
        pos += 1

    await session.commit()
    await session.refresh(o)
    return _out(o)


class DecisionIn(BaseModel):
    note: str | None = None


@router.post("/{oid}/approve")
async def approve(
    oid: uuid.UUID,
    body: DecisionIn,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.update))
    ],
) -> dict[str, Any]:
    cid = _company(current_user)
    o = await _load(session, cid, oid)
    if o.status != "requested":
        raise HTTPException(status_code=409, detail=f"This one is already {o.status}.")
    o.status = "approved"
    o.decided_by = getattr(current_user, "id", None)
    o.decided_at = _now()
    o.decision_note = (body.note or "").strip() or None
    await session.commit()
    await session.refresh(o)
    return _out(o)


@router.post("/{oid}/reject")
async def reject(
    oid: uuid.UUID,
    body: DecisionIn,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.update))
    ],
) -> dict[str, Any]:
    cid = _company(current_user)
    o = await _load(session, cid, oid)
    if o.status != "requested":
        raise HTTPException(status_code=409, detail=f"This one is already {o.status}.")
    o.status = "rejected"
    o.decided_by = getattr(current_user, "id", None)
    o.decided_at = _now()
    o.decision_note = (body.note or "").strip() or None
    await session.commit()
    await session.refresh(o)
    return _out(o)


@router.post("/{oid}/cancel")
async def cancel(
    oid: uuid.UUID,
    body: DecisionIn,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.update))
    ],
) -> dict[str, Any]:
    """They are staying. Distinct from rejected, which is a refusal of the request."""
    cid = _company(current_user)
    o = await _load(session, cid, oid)
    if o.status in ("completed", "cancelled"):
        raise HTTPException(status_code=409, detail=f"This one is already {o.status}.")
    o.status = "cancelled"
    o.decision_note = (body.note or "").strip() or None
    await session.commit()
    await session.refresh(o)
    return _out(o)


class TaskDoneIn(BaseModel):
    done: bool = True
    note: str | None = None
    # Only read for a task that carries an asset. The condition the thing came back in decides
    # where it goes next, so it belongs to this action rather than a later edit.
    return_condition: Literal["good", "damaged", "unusable", "not_returned"] = "good"


@router.post("/{oid}/tasks/{task_id}")
async def set_task(
    oid: uuid.UUID,
    task_id: uuid.UUID,
    body: TaskDoneIn,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.update))
    ],
) -> dict[str, Any]:
    """Tick a checklist item — and make it actually happen where it can.

    Two tasks do real work rather than recording an intention:

      * an asset task closes the assignment and sets the asset's status from its condition;
      * "Revoke system access" deactivates the person's login, if they have one.

    A checklist whose ticks change nothing is a checklist people stop trusting, and then stop
    filling in.
    """
    cid = _company(current_user)
    o = await _load(session, cid, oid)
    if o.status in ("rejected", "cancelled"):
        raise HTTPException(status_code=409, detail=f"This offboarding is {o.status}.")

    t = next((x for x in o.tasks if x.id == task_id), None)
    if t is None:
        raise HTTPException(status_code=404, detail="Task not found")

    if body.done and not t.done:
        if t.asset_id:
            assignment = (
                await session.execute(
                    select(AssetAssignment).where(
                        AssetAssignment.asset_id == t.asset_id,
                        AssetAssignment.employee_id == o.employee_id,
                        AssetAssignment.returned_at.is_(None),
                        AssetAssignment.company_id == cid,
                    )
                )
            ).scalar_one_or_none()
            if assignment is not None:
                assignment.returned_at = _now()
                assignment.returned_to = getattr(current_user, "id", None)
                assignment.return_condition = body.return_condition
                asset = (
                    await session.execute(select(Asset).where(Asset.id == t.asset_id))
                ).scalar_one_or_none()
                if asset is not None:
                    asset.status = {
                        "good": "available",
                        "damaged": "in_repair",
                        "unusable": "retired",
                        "not_returned": "lost",
                    }[body.return_condition]

        if t.title == "Revoke system access":
            login = (
                await session.execute(
                    select(EnterpriseUser).where(
                        # Matched on email because an Employee and its login are separate
                        # records in Croar; there is no foreign key between them.
                        func.lower(EnterpriseUser.email) == func.lower(o.employee.email),
                        EnterpriseUser.company_id == cid,
                        EnterpriseUser.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if login is not None:
                login.is_active = False

    t.done = body.done
    t.done_at = _now() if body.done else None
    t.done_by = getattr(current_user, "id", None) if body.done else None
    if body.note:
        t.note = body.note.strip()

    # The first tick moves an approved offboarding into progress, so the list shows what is
    # actually being worked rather than a pile that all reads "approved".
    if body.done and o.status == "approved":
        o.status = "in_progress"

    await session.commit()
    await session.refresh(o)
    return _out(o)


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    detail: str | None = None
    category: Literal["assets", "access", "finance", "hr", "knowledge"] = "hr"
    required: bool = False
    due_on: datetime | None = None


@router.post("/{oid}/tasks", status_code=201)
async def add_task(
    oid: uuid.UUID,
    body: TaskIn,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.update))
    ],
) -> dict[str, Any]:
    """Add something the standard list did not anticipate."""
    cid = _company(current_user)
    o = await _load(session, cid, oid)
    last = max((t.position for t in o.tasks), default=-1)
    session.add(
        OffboardingTask(
            offboarding_id=o.id,
            title=body.title.strip(),
            detail=(body.detail or "").strip() or None,
            category=body.category,
            required=body.required,
            due_on=_naive(body.due_on),
            position=last + 1,
            company_id=cid,
        )
    )
    await session.commit()
    await session.refresh(o)
    return _out(o)


@router.delete("/{oid}/tasks/{task_id}", status_code=204)
async def remove_task(
    oid: uuid.UUID,
    task_id: uuid.UUID,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.update))
    ],
) -> None:
    """Remove a task — except an asset one, which would drop the only record of the thing.

    Deleting "return the MacBook" makes the laptop disappear from the exit rather than from the
    person's hands. If it genuinely is not coming back, tick it as not returned instead: that
    marks the asset lost, which is the truth.
    """
    cid = _company(current_user)
    o = await _load(session, cid, oid)
    t = next((x for x in o.tasks if x.id == task_id), None)
    if t is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if t.asset_id:
        raise HTTPException(
            status_code=409,
            detail="This one is a company asset. Mark it returned, or not returned, instead of removing it.",
        )
    await session.delete(t)
    await session.commit()


@router.post("/{oid}/complete")
async def complete(
    oid: uuid.UUID,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.update))
    ],
) -> dict[str, Any]:
    """Close the exit — refused while a required task is open.

    This gate is the reason the module exists rather than a "left" flag on the employee: it is
    what stops somebody walking out with a laptop and a live login.

    The employee is marked inactive here rather than deleted. They worked here; the payroll,
    the assessments and the history all still point at them, and a deleted row takes all of it
    with it.
    """
    cid = _company(current_user)
    o = await _load(session, cid, oid)
    if o.status not in ("approved", "in_progress"):
        raise HTTPException(status_code=409, detail=f"This one is {o.status}, not in progress.")

    blocking = [t.title for t in o.tasks if t.required and not t.done]
    if blocking:
        raise HTTPException(status_code=409, detail="Still to do: " + ", ".join(blocking))

    o.status = "completed"
    o.completed_at = _now()
    if o.employee is not None:
        # "Inactive" with a capital, matching the "Active" the employee schema defaults to.
        # Two casings of the same status is how a filter silently starts missing rows.
        o.employee.status = "Inactive"
    await session.commit()
    await session.refresh(o)
    return _out(o)


@router.get("/{oid}")
async def get_one(
    oid: uuid.UUID,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.read))],
) -> dict[str, Any]:
    cid = _company(current_user)
    return _out(await _load(session, cid, oid))
