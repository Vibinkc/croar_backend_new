"""Attendance — who turned up, when, and what to do when the log is wrong.

The model shipped earlier with no way to reach it. This is that way.

Three audiences, three groups of endpoints:

  * **The employee** punches in and out, sees their own month, and raises a regularization when
    a punch was missed. They can never edit a day directly — that is the whole point of having
    a request path.
  * **HR** sees everyone, marks a day by hand when they have to, and decides regularizations.
  * **Payroll** asks one question — how many days was this person present in a period — which is
    what ``summary`` answers, and what payroll has until now been inferring from timesheets.

Punching is deliberately forgiving. A second "in" on a day that is already open does not error;
it is recorded and the day keeps its first in. Refusing would mean an employee who taps twice
gets an error message instead of a day's attendance, and the log keeps both events anyway.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.attendance import (
    ATTENDANCE_SOURCES,
    ATTENDANCE_STATUSES,
    PUNCH_DIRECTIONS,
    REGULARIZATION_STATUSES,
    WORK_MODES,
    AttendanceDay,
    AttendancePunch,
    AttendanceRegularization,
    Shift,
)
from app.models.enterprise.employee import Employee
from app.models.shared.constants import ModuleScope, PermissionAction

router = APIRouter(prefix="/attendance", tags=["Attendance"])

Status = Literal[ATTENDANCE_STATUSES]  # type: ignore[valid-type]
WorkMode = Literal[WORK_MODES]  # type: ignore[valid-type]

Reader = Annotated[object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.read))]
Writer = Annotated[object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.update))]
Creator = Annotated[object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.create))]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
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


async def _employee_for(session: Any, user: object, company_id: Any) -> Employee:
    """The Employee row behind the signed-in user, matched on email.

    Employees and users are separate records in Croar; the link is the address. A user with no
    employee record gets a 404 rather than an empty month, because an empty month reads as
    "you were absent all month" and that is a worse lie than an error.
    """
    email = getattr(user, "email", None)
    if not email:
        raise HTTPException(status_code=404, detail="No email on this account.")
    emp = (
        await session.execute(
            select(Employee).where(
                Employee.company_id == company_id,
                func.lower(Employee.email) == str(email).lower(),
                Employee.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if emp is None:
        raise HTTPException(
            status_code=404,
            detail="This account is not linked to an employee record, so it has no attendance.",
        )
    return emp


async def _resolve_shift(session: Any, company_id: Any, shift_id: uuid.UUID | None) -> Shift | None:
    """The named shift, else the company default, else nothing."""
    stmt = select(Shift).where(Shift.company_id == company_id, Shift.deleted_at.is_(None))
    if shift_id is not None:
        shift = (await session.execute(stmt.where(Shift.id == shift_id))).scalar_one_or_none()
        if shift is None:
            raise HTTPException(status_code=404, detail="Shift not found.")
        return shift
    return (await session.execute(stmt.where(Shift.is_default.is_(True)))).scalars().first()


def _recompute(day: AttendanceDay, punches: list[AttendancePunch], shift: Shift | None) -> None:
    """Derive the day's summary from its punches.

    first_in / last_out are the outer bounds, and worked minutes are the sum of in→out pairs
    rather than last_out minus first_in — otherwise a two-hour lunch counts as worked time.

    An odd number of punches (someone in, never out) leaves the day open: worked minutes count
    only the closed pairs. Guessing an out time would put invented hours into payroll.
    """
    ordered = sorted(punches, key=lambda p: p.punched_at)
    ins = [p for p in ordered if p.direction == "in"]
    outs = [p for p in ordered if p.direction == "out"]

    day.first_in = ins[0].punched_at if ins else None
    day.last_out = outs[-1].punched_at if outs else None

    worked = timedelta()
    open_in: datetime | None = None
    for p in ordered:
        if p.direction == "in":
            # Keep the earliest of a repeated in — a double tap should not restart the clock.
            open_in = open_in or p.punched_at
        elif open_in is not None:
            worked += p.punched_at - open_in
            open_in = None
    day.work_minutes = max(0, int(worked.total_seconds() // 60))

    if shift is not None and day.first_in is not None:
        scheduled = datetime.combine(day.work_date, shift.starts_at)
        day.late_minutes = max(0, int((day.first_in - scheduled).total_seconds() // 60))
        if day.work_minutes >= shift.full_day_after_minutes:
            day.status = "present"
        elif day.work_minutes >= shift.half_day_after_minutes:
            day.status = "half_day"
        else:
            # Still on the clock: the day is present until it closes short.
            day.status = "present" if day.last_out is None else "absent"
    elif day.first_in is not None:
        day.status = "present"


def _day_out(day: AttendanceDay, shift_names: dict[uuid.UUID, str]) -> dict[str, Any]:
    return {
        "id": str(day.id),
        "employee_id": str(day.employee_id),
        "work_date": day.work_date.isoformat(),
        "shift_id": str(day.shift_id) if day.shift_id else None,
        "shift": shift_names.get(day.shift_id) if day.shift_id else None,
        "status": day.status,
        "work_mode": day.work_mode,
        "source": day.source,
        "first_in": day.first_in.isoformat() if day.first_in else None,
        "last_out": day.last_out.isoformat() if day.last_out else None,
        "work_minutes": day.work_minutes,
        "work_hours": round(day.work_minutes / 60, 2),
        "late_minutes": day.late_minutes,
        "note": day.note,
        "locked": day.locked,
        # The relationship is selectin-loaded and ordered by punch time, so this is
        # safe to read directly in an async request.
        "punches": [
            {
                "id": str(p.id),
                "direction": p.direction,
                "punched_at": p.punched_at.isoformat(),
                "source": p.source,
                "location": p.location,
                "note": p.note,
            }
            for p in (day.punches or [])
        ],
    }


# ---------------------------------------------------------------------------
# Shifts
# ---------------------------------------------------------------------------
class ShiftIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    starts_at: time
    ends_at: time
    break_minutes: int = Field(default=60, ge=0, le=480)
    half_day_after_minutes: int = Field(default=240, ge=0, le=1440)
    full_day_after_minutes: int = Field(default=480, ge=0, le=1440)
    working_days: str = Field(default="01234", max_length=7)
    is_default: bool = False


@router.get("/shifts")
async def list_shifts(session: DBSessionDep, current_user: Reader) -> list[dict[str, Any]]:
    cid = _company(current_user)
    rows = (
        (
            await session.execute(
                select(Shift)
                .where(Shift.company_id == cid, Shift.deleted_at.is_(None))
                .order_by(Shift.is_default.desc(), Shift.name)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(s.id),
            "name": s.name,
            "starts_at": s.starts_at.isoformat(),
            "ends_at": s.ends_at.isoformat(),
            "break_minutes": s.break_minutes,
            "half_day_after_minutes": s.half_day_after_minutes,
            "full_day_after_minutes": s.full_day_after_minutes,
            "working_days": s.working_days,
            "is_default": s.is_default,
        }
        for s in rows
    ]


@router.post("/shifts", status_code=201)
async def create_shift(payload: ShiftIn, session: DBSessionDep, current_user: Creator) -> dict[str, Any]:
    cid = _company(current_user)
    if payload.half_day_after_minutes > payload.full_day_after_minutes:
        raise HTTPException(status_code=400, detail="A half day cannot need more minutes than a full day.")
    if payload.is_default:
        await _clear_default(session, cid)
    shift = Shift(company_id=cid, **payload.model_dump())
    session.add(shift)
    await session.commit()
    await session.refresh(shift)
    return {"id": str(shift.id), "name": shift.name}


@router.patch("/shifts/{shift_id}")
async def update_shift(
    shift_id: uuid.UUID, payload: ShiftIn, session: DBSessionDep, current_user: Writer
) -> dict[str, Any]:
    cid = _company(current_user)
    shift = (
        await session.execute(
            select(Shift).where(Shift.id == shift_id, Shift.company_id == cid, Shift.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if shift is None:
        raise HTTPException(status_code=404, detail="Shift not found.")
    if payload.is_default and not shift.is_default:
        await _clear_default(session, cid)
    for key, value in payload.model_dump().items():
        setattr(shift, key, value)
    await session.commit()
    return {"id": str(shift.id), "name": shift.name}


@router.delete("/shifts/{shift_id}", status_code=204)
async def delete_shift(shift_id: uuid.UUID, session: DBSessionDep, current_user: Writer) -> None:
    cid = _company(current_user)
    shift = (
        await session.execute(
            select(Shift).where(Shift.id == shift_id, Shift.company_id == cid, Shift.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if shift is None:
        raise HTTPException(status_code=404, detail="Shift not found.")
    shift.deleted_at = _now()
    await session.commit()


async def _clear_default(session: Any, company_id: Any) -> None:
    """Only one default shift per company; setting a new one demotes the old."""
    for s in (
        (
            await session.execute(
                select(Shift).where(
                    Shift.company_id == company_id, Shift.is_default.is_(True), Shift.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    ):
        s.is_default = False


# ---------------------------------------------------------------------------
# Punching — the employee's own day
# ---------------------------------------------------------------------------
class PunchIn(BaseModel):
    direction: Literal[PUNCH_DIRECTIONS]  # type: ignore[valid-type]
    work_mode: WorkMode | None = None
    location: str | None = Field(default=None, max_length=120)
    note: str | None = None
    at: datetime | None = None  # Defaults to now; supplied by biometric/import feeds.
    source: Literal[ATTENDANCE_SOURCES] = "web"  # type: ignore[valid-type]


async def _load_or_open_day(
    session: Any, company_id: Any, employee_id: uuid.UUID, work_date: date
) -> AttendanceDay:
    day = (
        await session.execute(
            select(AttendanceDay)
            .options(selectinload(AttendanceDay.punches))
            .where(
                AttendanceDay.company_id == company_id,
                AttendanceDay.employee_id == employee_id,
                AttendanceDay.work_date == work_date,
            )
        )
    ).scalar_one_or_none()
    if day is None:
        day = AttendanceDay(
            company_id=company_id,
            employee_id=employee_id,
            work_date=work_date,
            status="absent",
            # Seeded here rather than after the flush. Assigning to the collection on an
            # already-flushed instance makes SQLAlchemy load the existing rows first to work
            # out the delta, and that lazy load inside an async request raises MissingGreenlet.
            punches=[],
        )
        session.add(day)
        await session.flush()
    return day


@router.post("/punch", status_code=201)
async def punch(payload: PunchIn, session: DBSessionDep, current_user: Reader) -> dict[str, Any]:
    """Record an in or out for the signed-in employee.

    Read permission, not write: punching your own clock is not an administrative act, and
    requiring update rights would mean every employee needs the permission to edit everyone's
    record just to say they arrived.
    """
    cid = _company(current_user)
    emp = await _employee_for(session, current_user, cid)
    at = _naive(payload.at) or _now()
    work_date = at.date()

    day = await _load_or_open_day(session, cid, emp.id, work_date)
    if day.locked:
        raise HTTPException(
            status_code=409,
            detail="This day is locked because payroll has used it. Raise a regularization instead.",
        )

    shift = await _resolve_shift(session, cid, day.shift_id)
    if day.shift_id is None and shift is not None:
        day.shift_id = shift.id
    if payload.work_mode:
        day.work_mode = payload.work_mode
    day.source = payload.source

    p = AttendancePunch(
        company_id=cid,
        day_id=day.id,
        direction=payload.direction,
        punched_at=at,
        source=payload.source,
        location=payload.location,
        note=payload.note,
    )
    session.add(p)
    await session.flush()

    _recompute(day, [*day.punches, p], shift)
    await session.commit()
    return {
        "day_id": str(day.id),
        "work_date": work_date.isoformat(),
        "direction": payload.direction,
        "punched_at": at.isoformat(),
        "status": day.status,
        "work_minutes": day.work_minutes,
    }


@router.get("/me")
async def my_attendance(
    session: DBSessionDep,
    current_user: Reader,
    month: str | None = Query(default=None, description="YYYY-MM; defaults to the current month"),
) -> dict[str, Any]:
    """The signed-in employee's own month, plus whether they are currently punched in."""
    cid = _company(current_user)
    emp = await _employee_for(session, current_user, cid)
    start, end = _month_bounds(month)

    days = (
        (
            await session.execute(
                select(AttendanceDay)
                .options(selectinload(AttendanceDay.punches))
                .where(
                    AttendanceDay.company_id == cid,
                    AttendanceDay.employee_id == emp.id,
                    AttendanceDay.work_date >= start,
                    AttendanceDay.work_date <= end,
                )
                .order_by(AttendanceDay.work_date.desc())
            )
        )
        .scalars()
        .all()
    )
    shift_names = await _shift_names(session, cid)
    today = next((d for d in days if d.work_date == date.today()), None)
    open_now = bool(
        today and today.punches and sorted(today.punches, key=lambda p: p.punched_at)[-1].direction == "in"
    )
    return {
        "employee_id": str(emp.id),
        "month": start.strftime("%Y-%m"),
        "punched_in": open_now,
        "today": _day_out(today, shift_names) if today else None,
        "days": [_day_out(d, shift_names) for d in days],
        "totals": _totals(days),
    }


# ---------------------------------------------------------------------------
# HR view
# ---------------------------------------------------------------------------
def _month_bounds(month: str | None) -> tuple[date, date]:
    today = date.today()
    if month:
        try:
            start = datetime.strptime(month, "%Y-%m").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="month must look like 2026-09.") from None
    else:
        start = today.replace(day=1)
    # First of the next month, minus a day.
    nxt = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, nxt - timedelta(days=1)


def _totals(days: Sequence[AttendanceDay]) -> dict[str, Any]:
    counts = dict.fromkeys(ATTENDANCE_STATUSES, 0)
    minutes = 0
    for d in days:
        counts[d.status] = counts.get(d.status, 0) + 1
        minutes += d.work_minutes
    return {
        **counts,
        # What payroll actually asks for: a half day is half a day present.
        "payable_days": counts.get("present", 0) + counts.get("half_day", 0) * 0.5,
        "work_hours": round(minutes / 60, 2),
    }


async def _shift_names(session: Any, company_id: Any) -> dict[uuid.UUID, str]:
    rows = (await session.execute(select(Shift).where(Shift.company_id == company_id))).scalars().all()
    return {s.id: s.name for s in rows}


@router.get("")
async def list_attendance(
    session: DBSessionDep,
    current_user: Reader,
    month: str | None = None,
    employee_id: uuid.UUID | None = None,
    status: Status | None = None,
) -> dict[str, Any]:
    """Everyone's attendance for a month, optionally narrowed to one person or status."""
    cid = _company(current_user)
    start, end = _month_bounds(month)

    stmt = (
        select(AttendanceDay)
        .options(selectinload(AttendanceDay.punches))
        .where(
            AttendanceDay.company_id == cid, AttendanceDay.work_date >= start, AttendanceDay.work_date <= end
        )
    )
    if employee_id is not None:
        stmt = stmt.where(AttendanceDay.employee_id == employee_id)
    if status is not None:
        stmt = stmt.where(AttendanceDay.status == status)

    days = (await session.execute(stmt.order_by(AttendanceDay.work_date.desc()))).scalars().all()
    employees = {
        e.id: e
        for e in (await session.execute(select(Employee).where(Employee.company_id == cid))).scalars().all()
    }
    shift_names = await _shift_names(session, cid)

    out = []
    for d in days:
        e = employees.get(d.employee_id)
        out.append(
            {
                **_day_out(d, shift_names),
                "employee_name": f"{e.first_name} {e.last_name}".strip() if e else "",
                "employee_code": (e.employee_id if e else ""),
            }
        )
    return {"month": start.strftime("%Y-%m"), "days": out, "totals": _totals(days)}


class MarkIn(BaseModel):
    employee_id: uuid.UUID
    work_date: date
    status: Status
    work_mode: WorkMode | None = None
    shift_id: uuid.UUID | None = None
    first_in: datetime | None = None
    last_out: datetime | None = None
    note: str | None = None


@router.post("/mark", status_code=201)
async def mark_day(payload: MarkIn, session: DBSessionDep, current_user: Writer) -> dict[str, Any]:
    """HR sets a day by hand. Recorded as source "manual" so an audit can tell it apart."""
    cid = _company(current_user)
    emp = (
        await session.execute(
            select(Employee).where(
                Employee.id == payload.employee_id, Employee.company_id == cid, Employee.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found.")

    day = await _load_or_open_day(session, cid, payload.employee_id, payload.work_date)
    if day.locked:
        raise HTTPException(status_code=409, detail="This day is locked because payroll has used it.")

    day.status = payload.status
    day.source = "manual"
    if payload.work_mode:
        day.work_mode = payload.work_mode
    if payload.shift_id is not None:
        day.shift_id = payload.shift_id
    if payload.first_in is not None:
        day.first_in = _naive(payload.first_in)
    if payload.last_out is not None:
        day.last_out = _naive(payload.last_out)
    if day.first_in and day.last_out:
        day.work_minutes = max(0, int((day.last_out - day.first_in).total_seconds() // 60))
    day.note = payload.note
    await session.commit()
    return {"id": str(day.id), "work_date": day.work_date.isoformat(), "status": day.status}


@router.get("/summary")
async def attendance_summary(
    session: DBSessionDep, current_user: Reader, period_start: date, period_end: date
) -> list[dict[str, Any]]:
    """Payable days per employee for a period — the question payroll asks.

    Payroll currently derives presence from timesheets. This is the number it should use
    instead, and it exists so that switching is a one-line change rather than a rewrite.
    """
    cid = _company(current_user)
    if period_end < period_start:
        raise HTTPException(status_code=400, detail="period_end falls before period_start.")

    days = (
        (
            await session.execute(
                select(AttendanceDay).where(
                    AttendanceDay.company_id == cid,
                    AttendanceDay.work_date >= period_start,
                    AttendanceDay.work_date <= period_end,
                )
            )
        )
        .scalars()
        .all()
    )
    employees = {
        e.id: e
        for e in (await session.execute(select(Employee).where(Employee.company_id == cid))).scalars().all()
    }

    grouped: dict[uuid.UUID, list[AttendanceDay]] = {}
    for d in days:
        grouped.setdefault(d.employee_id, []).append(d)

    out = []
    for employee_id, rows in grouped.items():
        e = employees.get(employee_id)
        totals = _totals(rows)
        out.append(
            {
                "employee_id": str(employee_id),
                "employee_name": f"{e.first_name} {e.last_name}".strip() if e else "",
                "employee_code": (e.employee_id if e else ""),
                "payable_days": totals["payable_days"],
                "present": totals.get("present", 0),
                "half_day": totals.get("half_day", 0),
                "absent": totals.get("absent", 0),
                "leave": totals.get("leave", 0),
                "work_hours": totals["work_hours"],
            }
        )
    out.sort(key=lambda r: str(r["employee_name"]).lower())
    return out


# ---------------------------------------------------------------------------
# Regularization — the request path
# ---------------------------------------------------------------------------
class RegularizationIn(BaseModel):
    work_date: date
    requested_status: Status
    requested_in: datetime | None = None
    requested_out: datetime | None = None
    requested_work_mode: WorkMode | None = None
    reason: str = Field(min_length=1)


@router.post("/regularizations", status_code=201)
async def request_regularization(
    payload: RegularizationIn, session: DBSessionDep, current_user: Reader
) -> dict[str, Any]:
    """An employee asks for a day to be corrected. Read permission — it is a request, not an edit."""
    cid = _company(current_user)
    emp = await _employee_for(session, current_user, cid)
    if payload.work_date > date.today():
        raise HTTPException(status_code=400, detail="You cannot regularize a day that has not happened.")

    existing = (
        await session.execute(
            select(AttendanceRegularization).where(
                AttendanceRegularization.company_id == cid,
                AttendanceRegularization.employee_id == emp.id,
                AttendanceRegularization.work_date == payload.work_date,
                AttendanceRegularization.status == "pending",
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="There is already a pending request for that day.")

    req = AttendanceRegularization(
        company_id=cid,
        employee_id=emp.id,
        work_date=payload.work_date,
        requested_status=payload.requested_status,
        requested_in=_naive(payload.requested_in),
        requested_out=_naive(payload.requested_out),
        requested_work_mode=payload.requested_work_mode,
        reason=payload.reason,
        status="pending",
        requested_by=getattr(current_user, "id", None),
        requested_at=_now(),
    )
    session.add(req)
    await session.commit()
    await session.refresh(req)
    return {"id": str(req.id), "status": req.status, "work_date": req.work_date.isoformat()}


@router.get("/regularizations")
async def list_regularizations(
    session: DBSessionDep,
    current_user: Reader,
    status: Literal[REGULARIZATION_STATUSES] | None = None,  # type: ignore[valid-type]
    mine: bool = False,
) -> list[dict[str, Any]]:
    cid = _company(current_user)
    stmt = select(AttendanceRegularization).where(AttendanceRegularization.company_id == cid)
    if mine:
        emp = await _employee_for(session, current_user, cid)
        stmt = stmt.where(AttendanceRegularization.employee_id == emp.id)
    if status is not None:
        stmt = stmt.where(AttendanceRegularization.status == status)

    rows = (
        (await session.execute(stmt.order_by(AttendanceRegularization.requested_at.desc()))).scalars().all()
    )
    employees = {
        e.id: e
        for e in (await session.execute(select(Employee).where(Employee.company_id == cid))).scalars().all()
    }
    return [
        {
            "id": str(r.id),
            "employee_id": str(r.employee_id),
            "employee_name": (
                f"{employees[r.employee_id].first_name} {employees[r.employee_id].last_name}".strip()
                if r.employee_id in employees
                else ""
            ),
            "work_date": r.work_date.isoformat(),
            "requested_status": r.requested_status,
            "requested_in": r.requested_in.isoformat() if r.requested_in else None,
            "requested_out": r.requested_out.isoformat() if r.requested_out else None,
            "requested_work_mode": r.requested_work_mode,
            "reason": r.reason,
            "status": r.status,
            "requested_at": r.requested_at.isoformat() if r.requested_at else None,
            "decided_at": r.decided_at.isoformat() if r.decided_at else None,
            "decision_note": r.decision_note,
        }
        for r in rows
    ]


class DecisionIn(BaseModel):
    decision: Literal["approved", "rejected"]
    note: str | None = None


@router.post("/regularizations/{request_id}/decide")
async def decide_regularization(
    request_id: uuid.UUID, payload: DecisionIn, session: DBSessionDep, current_user: Writer
) -> dict[str, Any]:
    """Approve or reject. Approving is what actually writes the corrected day.

    The day is stamped source "regularization" rather than "manual", so the register can still
    distinguish a correction an employee asked for from one HR made unprompted.
    """
    cid = _company(current_user)
    req = (
        await session.execute(
            select(AttendanceRegularization).where(
                AttendanceRegularization.id == request_id, AttendanceRegularization.company_id == cid
            )
        )
    ).scalar_one_or_none()
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found.")
    if req.status != "pending":
        raise HTTPException(status_code=409, detail=f"This request was already {req.status}.")

    req.status = payload.decision
    req.decided_by = getattr(current_user, "id", None)
    req.decided_at = _now()
    req.decision_note = payload.note

    if payload.decision == "approved":
        day = await _load_or_open_day(session, cid, req.employee_id, req.work_date)
        if day.locked:
            raise HTTPException(
                status_code=409,
                detail="That day is locked because payroll has used it and cannot be corrected now.",
            )
        day.status = req.requested_status
        day.source = "regularization"
        if req.requested_in is not None:
            day.first_in = req.requested_in
        if req.requested_out is not None:
            day.last_out = req.requested_out
        if req.requested_work_mode:
            day.work_mode = req.requested_work_mode
        if day.first_in and day.last_out:
            day.work_minutes = max(0, int((day.last_out - day.first_in).total_seconds() // 60))
        day.note = f"Regularized: {req.reason}"

    await session.commit()
    return {"id": str(req.id), "status": req.status}
