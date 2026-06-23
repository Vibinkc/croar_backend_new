"""Timesheet lifecycle + attendance aggregation.

A timesheet is one employee's attendance for one payroll cycle. It is seeded
(``generate_for_cycle``) with one entry per calendar day, edited by HR, then
SUBMITTED and APPROVED. Only APPROVED timesheets feed a payroll run — see
``get_approved`` and payroll_service.run_payroll.

The aggregate columns (worked_days / lop_days / half_days / total_hours) are
recomputed from the daily entries on every edit so the run can read them without
re-walking the entries.
"""

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enterprise.employee import Employee
from app.models.payroll import PayrollCycle, SalaryStructure
from app.models.payroll.timesheets import Timesheet, TimesheetEntry
from app.payroll.constants import (
    WEEKDAY_CODES,
    DayStatus,
    PayFrequency,
    PayrollCycleStatus,
    TimesheetMode,
    TimesheetStatus,
)
from app.services.payroll import calendar_service, leave_service

# Day statuses that consume a scheduled working day as loss-of-pay.
_FULL_LOP = {DayStatus.UNPAID_LEAVE.value}
# Days that are not scheduled work (never count toward working days or LOP).
_NON_WORKING = {DayStatus.WEEKLY_OFF.value, DayStatus.HOLIDAY.value}
_HALF = DayStatus.HALF_DAY.value
# Half worked + half *paid* leave: counts as a half-day for display but adds no
# LOP (the off-half is covered by a paid leave type).
_HALF_PAID = DayStatus.HALF_DAY_PAID.value

# Timesheet states in which the daily grid may still be edited.
_EDITABLE_STATES = {TimesheetStatus.DRAFT.value, TimesheetStatus.REJECTED.value}
_EDITABLE_CYCLE = {PayrollCycleStatus.DRAFT, PayrollCycleStatus.PROCESSING}


async def _load_cycle(db: AsyncSession, cycle_id: uuid.UUID, company_id: uuid.UUID) -> PayrollCycle:
    cycle = (
        await db.execute(
            select(PayrollCycle).where(
                PayrollCycle.id == cycle_id,
                PayrollCycle.company_id == company_id,
                PayrollCycle.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if not cycle:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cycle not found.")
    return cycle


def recompute_aggregates(ts: Timesheet) -> None:
    """Recompute the cached aggregates from the timesheet's loaded entries.

    - lop_days   = full unpaid days + 0.5 * unpaid half-days
    - worked_days = scheduled working days - lop_days  (paid days)
    - half_days  = count of half-day entries (paid + unpaid)
    - total_hours = sum of logged hours (HOURLY mode)

    A paid half-day (HALF_DAY_PAID) is a half-day for display but contributes no
    LOP — only the unpaid HALF_DAY adds 0.5.
    """
    scheduled = Decimal("0")
    lop = Decimal("0")
    halves = Decimal("0")
    hours = Decimal("0")
    for e in ts.entries:
        if e.hours is not None:
            hours += e.hours
        if e.day_status in _NON_WORKING:
            continue
        scheduled += Decimal("1")
        if e.day_status in _FULL_LOP:
            lop += Decimal("1")
        elif e.day_status == _HALF:
            halves += Decimal("1")
            lop += Decimal("0.5")
        elif e.day_status == _HALF_PAID:
            halves += Decimal("1")  # paid off-half: no LOP
    ts.lop_days = lop
    ts.half_days = halves
    ts.worked_days = scheduled - lop
    ts.total_hours = hours


async def _employee_mode(db: AsyncSession, company_id: uuid.UUID, employee_id: uuid.UUID) -> str:
    """ATTENDANCE unless the employee's active structure is HOURLY."""
    freq = (
        await db.execute(
            select(SalaryStructure.pay_frequency).where(
                SalaryStructure.employee_id == employee_id,
                SalaryStructure.company_id == company_id,
                SalaryStructure.is_active.is_(True),
                SalaryStructure.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if str(freq) == PayFrequency.HOURLY.value:
        return TimesheetMode.HOURLY.value
    return TimesheetMode.ATTENDANCE.value


async def generate_for_cycle(db: AsyncSession, company_id: uuid.UUID, cycle_id: uuid.UUID) -> dict:
    """Create a timesheet (+ seeded daily entries) for every active employee that
    has an active salary structure and doesn't already have one for this cycle.

    Idempotent: existing timesheets are left untouched (counted as ``existing``).
    Each scheduled working day defaults to PRESENT (full attendance ⇒ zero LOP, so
    a run is identical to today's no-LOP behaviour until HR edits the sheet);
    weekly-offs/holidays are marked as such and never count as LOP.
    """
    cycle = await _load_cycle(db, cycle_id, company_id)

    employees = (
        (
            await db.execute(
                select(Employee).where(Employee.company_id == company_id, Employee.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    )

    existing_ids = set(
        (
            await db.execute(
                select(Timesheet.employee_id).where(
                    Timesheet.cycle_id == cycle_id,
                    Timesheet.company_id == company_id,
                    Timesheet.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    cfg = await calendar_service.load_calendar_config(db, company_id)
    weekly_offs = set(cfg.weekly_offs)
    holidays = await calendar_service.get_holiday_dates(db, company_id, cycle.period_start, cycle.period_end)

    created = 0
    existing = 0
    skipped: list[dict] = []
    for emp in employees:
        if emp.id in existing_ids:
            existing += 1
            continue
        # Only employees with an active structure are payable — skip the rest so
        # the timesheet list mirrors who a run would actually pay.
        has_struct = (
            await db.execute(
                select(SalaryStructure.id).where(
                    SalaryStructure.employee_id == emp.id,
                    SalaryStructure.company_id == company_id,
                    SalaryStructure.is_active.is_(True),
                    SalaryStructure.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if has_struct is None:
            skipped.append({"employee_id": str(emp.id), "reason": "no active salary structure"})
            continue

        mode = await _employee_mode(db, company_id, emp.id)
        # Overlay any APPROVED leave for this employee onto the seeded days so the
        # grid reflects the leave ledger out of the box (PAID_LEAVE / UNPAID_LEAVE
        # / HALF_DAY). Hourly timesheets don't carry day statuses, so skip.
        leave_days: dict = {}
        if mode != TimesheetMode.HOURLY.value:
            leave_days = await leave_service.get_approved_leave_days(
                db, company_id, emp.id, cycle.period_start, cycle.period_end
            )
        ts = Timesheet(
            company_id=company_id,
            cycle_id=cycle_id,
            employee_id=emp.id,
            period_start=cycle.period_start,
            period_end=cycle.period_end,
            mode=mode,
            status=TimesheetStatus.DRAFT.value,
        )
        ts.entries = _seed_entries(company_id, cycle, weekly_offs, holidays, leave_days)
        recompute_aggregates(ts)
        db.add(ts)
        created += 1

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return {"created": created, "existing": existing, "skipped": skipped}


def _seed_entries(
    company_id: uuid.UUID,
    cycle: PayrollCycle,
    weekly_offs: set[str],
    holidays: set[date],
    leave_days: dict[date, str] | None = None,
) -> list[TimesheetEntry]:
    leave_days = leave_days or {}
    entries: list[TimesheetEntry] = []
    day = cycle.period_start
    while day <= cycle.period_end:
        if WEEKDAY_CODES[day.weekday()] in weekly_offs:
            day_status = DayStatus.WEEKLY_OFF.value
        elif day in holidays:
            day_status = DayStatus.HOLIDAY.value
        elif day in leave_days:
            # Approved leave takes precedence over a plain working day.
            day_status = leave_days[day]
        else:
            day_status = DayStatus.PRESENT.value
        entries.append(TimesheetEntry(company_id=company_id, entry_date=day, day_status=day_status))
        day += timedelta(days=1)
    return entries


async def get_detail(db: AsyncSession, timesheet_id: uuid.UUID, company_id: uuid.UUID) -> Timesheet:
    ts = (
        await db.execute(
            select(Timesheet)
            .where(
                Timesheet.id == timesheet_id,
                Timesheet.company_id == company_id,
                Timesheet.deleted_at.is_(None),
            )
            .options(selectinload(Timesheet.entries))
        )
    ).scalar_one_or_none()
    if not ts:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Timesheet not found.")
    return ts


async def list_for_employee(
    db: AsyncSession, company_id: uuid.UUID, employee_id: uuid.UUID
) -> list[Timesheet]:
    """Every timesheet belonging to one employee, newest period first. Used by
    the self-service (/api/v1/me) endpoints — always scoped to the caller."""
    rows = (
        (
            await db.execute(
                select(Timesheet)
                .where(
                    Timesheet.company_id == company_id,
                    Timesheet.employee_id == employee_id,
                    Timesheet.deleted_at.is_(None),
                )
                .order_by(Timesheet.period_start.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def get_owned_detail(
    db: AsyncSession, timesheet_id: uuid.UUID, company_id: uuid.UUID, employee_id: uuid.UUID
) -> Timesheet:
    """Load a timesheet detail only if it belongs to `employee_id`.

    A mismatch raises 404 (not 403) so a self-service user can't probe which
    timesheet ids exist for other employees."""
    ts = await get_detail(db, timesheet_id, company_id)
    if ts.employee_id != employee_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Timesheet not found.")
    return ts


async def list_for_cycle(db: AsyncSession, company_id: uuid.UUID, cycle_id: uuid.UUID) -> list[Timesheet]:
    rows = (
        (
            await db.execute(
                select(Timesheet)
                .where(
                    Timesheet.company_id == company_id,
                    Timesheet.cycle_id == cycle_id,
                    Timesheet.deleted_at.is_(None),
                )
                .order_by(Timesheet.created_at)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def bulk_update_entries(
    db: AsyncSession, timesheet_id: uuid.UUID, company_id: uuid.UUID, entries_in: list
) -> Timesheet:
    """Apply per-day edits (status / hours / note), then recompute aggregates.

    Only allowed while the timesheet is DRAFT/REJECTED and its cycle is
    DRAFT/PROCESSING. Edits are matched to existing seeded entries by date; an
    edit for a date with no entry is ignored (the grid only shows real days)."""
    ts = await get_detail(db, timesheet_id, company_id)
    if ts.status not in _EDITABLE_STATES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Timesheet is {ts.status}; reopen it to edit (must be DRAFT or REJECTED).",
        )
    cycle = await _load_cycle(db, ts.cycle_id, company_id)
    if cycle.status not in _EDITABLE_CYCLE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot edit timesheet while cycle is {cycle.status}.",
        )

    by_date = {e.entry_date: e for e in ts.entries}
    for item in entries_in:
        entry = by_date.get(item.entry_date)
        if entry is None:
            continue
        if item.day_status is not None:
            entry.day_status = item.day_status.value
        if item.hours is not None:
            entry.hours = item.hours
        if item.note is not None:
            entry.note = item.note or None
    recompute_aggregates(ts)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return await get_detail(db, timesheet_id, company_id)


# Statuses an employee may set on their OWN day via self-mark. Deliberately
# excludes leave/LOP/half-day — an employee can't dock their own pay or grant
# themselves leave (that's the leave flow + HR). Just attendance: in, or WFH.
_SELF_MARKABLE = {DayStatus.PRESENT.value, DayStatus.WFH.value}
# Days driven by approved leave — self-mark must not clobber them.
_LEAVE_LOCKED = {
    DayStatus.PAID_LEAVE.value,
    DayStatus.UNPAID_LEAVE.value,
    DayStatus.HALF_DAY.value,
    DayStatus.HALF_DAY_PAID.value,
}


async def self_mark_entries(
    db: AsyncSession,
    timesheet_id: uuid.UUID,
    company_id: uuid.UUID,
    employee_id: uuid.UUID,
    entries_in: list,
    *,
    today: date,
) -> Timesheet:
    """Employee self-service attendance marking on their OWN timesheet.

    Reuses the daily grid but with tight guards vs. the HR ``bulk_update_entries``:
    ownership (404 if not theirs), only while DRAFT/REJECTED + cycle editable,
    only PRESENT/WFH statuses (no self-LOP / self-leave), never a future date, and
    never over a leave-stamped day. Hours may be logged (hourly mode). HR still
    submits/approves the sheet — this only edits the draft.
    """
    ts = await get_owned_detail(db, timesheet_id, company_id, employee_id)
    if ts.status not in _EDITABLE_STATES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Your timesheet is {ts.status} and can no longer be edited.",
        )
    cycle = await _load_cycle(db, ts.cycle_id, company_id)
    if cycle.status not in _EDITABLE_CYCLE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This period is {cycle.status}; attendance can no longer be marked.",
        )

    by_date = {e.entry_date: e for e in ts.entries}
    for item in entries_in:
        entry = by_date.get(item.entry_date)
        if entry is None:
            continue
        if entry.entry_date > today:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="You can't mark attendance for a future date.",
            )
        if entry.day_status in _NON_WORKING:
            continue  # weekly-off / holiday — nothing to mark
        if entry.day_status in _LEAVE_LOCKED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(f"{entry.entry_date} is on approved leave; cancel the leave request to change it."),
            )
        if item.day_status is not None:
            if item.day_status.value not in _SELF_MARKABLE:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="You can only mark a working day Present or Work From Home.",
                )
            entry.day_status = item.day_status.value
        if item.hours is not None:
            entry.hours = item.hours
        if item.note is not None:
            entry.note = item.note or None

    recompute_aggregates(ts)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return await get_owned_detail(db, timesheet_id, company_id, employee_id)


# CSV/biometric status token (uppercased) -> DayStatus. ABSENT/LOP land as a
# full unpaid (LOP) day; this is an HR-driven import so docking pay is allowed.
_IMPORT_STATUS = {
    "PRESENT": DayStatus.PRESENT.value,
    "P": DayStatus.PRESENT.value,
    "WFH": DayStatus.WFH.value,
    "WORK_FROM_HOME": DayStatus.WFH.value,
    "HALF_DAY": DayStatus.HALF_DAY.value,
    "HALF": DayStatus.HALF_DAY.value,
    "ABSENT": DayStatus.UNPAID_LEAVE.value,
    "A": DayStatus.UNPAID_LEAVE.value,
    "LOP": DayStatus.UNPAID_LEAVE.value,
}


def _parse_hhmm(value: str) -> int | None:
    """'09:05' -> minutes since midnight, or None if unparseable."""
    parts = value.split(":")
    if len(parts) != 2:
        return None
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m


async def import_attendance(
    db: AsyncSession, company_id: uuid.UUID, cycle_id: uuid.UUID, rows: list[dict]
) -> dict:
    """Bulk-import daily attendance (e.g. a biometric device export / CSV) onto a
    cycle's timesheets — the realistic high-volume capture path.

    Each row identifies an employee (``employee_code`` = Employee.employee_id, or
    ``employee_id`` UUID), a ``date``, and either a ``status`` (PRESENT/WFH/
    HALF_DAY/ABSENT) and/or punch times (``check_in``/``check_out`` HH:MM, from
    which hours are derived) or an explicit ``hours`` value. Overlays onto the
    matching ``TimesheetEntry`` exactly like the manual grid, then recomputes
    aggregates. Rows that can't be applied are reported in ``skipped`` (never
    fatal), so one bad line doesn't sink the whole file.
    """
    await _load_cycle(db, cycle_id, company_id)

    employees = (
        (
            await db.execute(
                select(Employee).where(Employee.company_id == company_id, Employee.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    by_key: dict[str, uuid.UUID] = {}
    for e in employees:
        by_key[str(e.id)] = e.id
        if e.employee_id:
            by_key[e.employee_id.strip().upper()] = e.id

    ts_rows = (
        (
            await db.execute(
                select(Timesheet)
                .where(
                    Timesheet.cycle_id == cycle_id,
                    Timesheet.company_id == company_id,
                    Timesheet.deleted_at.is_(None),
                )
                .options(selectinload(Timesheet.entries))
            )
        )
        .scalars()
        .all()
    )
    ts_by_emp = {t.employee_id: t for t in ts_rows}

    updated = 0
    skipped: list[dict] = []
    touched: dict[uuid.UUID, Timesheet] = {}

    def skip(i: int, reason: str) -> None:
        skipped.append({"row": i, "reason": reason})

    for i, row in enumerate(rows, start=1):
        key = (row.get("employee_code") or row.get("employee_id") or "").strip()
        if not key:
            skip(i, "missing employee_code")
            continue
        emp_id = by_key.get(key) or by_key.get(key.upper())
        if emp_id is None:
            skip(i, f"unknown employee '{key}'")
            continue
        ts = ts_by_emp.get(emp_id)
        if ts is None:
            skip(i, "no timesheet for this employee in the cycle")
            continue
        if ts.status not in _EDITABLE_STATES:
            skip(i, f"timesheet is {ts.status} (not editable)")
            continue
        try:
            d = date.fromisoformat((row.get("date") or "").strip())
        except ValueError:
            skip(i, f"bad or missing date '{row.get('date')}'")
            continue
        entry = next((e for e in ts.entries if e.entry_date == d), None)
        if entry is None:
            skip(i, "date outside the timesheet period")
            continue
        if entry.day_status in _NON_WORKING:
            skip(i, "non-working day (weekly-off/holiday)")
            continue
        if entry.day_status in _LEAVE_LOCKED:
            skip(i, "day is on approved leave")
            continue

        status_tok = (row.get("status") or "").strip().upper()
        check_in = (row.get("check_in") or "").strip()
        check_out = (row.get("check_out") or "").strip()
        hours_raw = (row.get("hours") or "").strip()

        if status_tok:
            mapped = _IMPORT_STATUS.get(status_tok)
            if mapped is None:
                skip(i, f"unknown status '{status_tok}'")
                continue
            entry.day_status = mapped
        elif check_in or check_out:
            entry.day_status = DayStatus.PRESENT.value  # a punch implies present

        if check_in and check_out:
            mins = None
            ci, co = _parse_hhmm(check_in), _parse_hhmm(check_out)
            if ci is not None and co is not None and co > ci:
                mins = co - ci
            if mins is None:
                skip(i, "bad check_in/check_out times")
                continue
            entry.hours = (Decimal(mins) / Decimal("60")).quantize(Decimal("0.01"))
        elif hours_raw:
            try:
                entry.hours = Decimal(hours_raw)
            except (ArithmeticError, ValueError):
                skip(i, f"bad hours '{hours_raw}'")
                continue

        touched[ts.id] = ts
        updated += 1

    for ts in touched.values():
        recompute_aggregates(ts)
    if touched:
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            raise
    return {"updated": updated, "skipped": skipped}


async def _transition(
    db: AsyncSession,
    timesheet_id: uuid.UUID,
    company_id: uuid.UUID,
    *,
    allowed_from: set[str],
    to: str,
    actor_id: uuid.UUID | None = None,
    set_submitted: bool = False,
    set_approved: bool = False,
    note: str | None = None,
) -> Timesheet:
    ts = await get_detail(db, timesheet_id, company_id)
    if ts.status not in allowed_from:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"Cannot move timesheet from {ts.status} to {to}."
        )
    if set_approved and actor_id is not None:
        cfg = await calendar_service.load_calendar_config(db, company_id)
        if cfg.enforce_maker_checker and ts.submitted_by_id == actor_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "You submitted this timesheet; a different user must approve it "
                    "(segregation of duties is enabled)."
                ),
            )
    ts.status = to
    now = datetime.utcnow()
    if set_submitted:
        ts.submitted_at = now
        ts.submitted_by_id = actor_id
    if set_approved:
        ts.approved_at = now
        ts.approved_by_id = actor_id
    if note is not None:
        ts.notes = note or None
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return await get_detail(db, timesheet_id, company_id)


async def submit(db, timesheet_id, company_id, actor_id=None) -> Timesheet:
    return await _transition(
        db,
        timesheet_id,
        company_id,
        allowed_from={TimesheetStatus.DRAFT.value, TimesheetStatus.REJECTED.value},
        to=TimesheetStatus.SUBMITTED.value,
        actor_id=actor_id,
        set_submitted=True,
    )


async def approve(db, timesheet_id, company_id, actor_id=None) -> Timesheet:
    return await _transition(
        db,
        timesheet_id,
        company_id,
        allowed_from={TimesheetStatus.SUBMITTED.value},
        to=TimesheetStatus.APPROVED.value,
        actor_id=actor_id,
        set_approved=True,
    )


async def reject(db, timesheet_id, company_id, note: str | None = None, actor_id=None) -> Timesheet:
    return await _transition(
        db,
        timesheet_id,
        company_id,
        allowed_from={TimesheetStatus.SUBMITTED.value},
        to=TimesheetStatus.REJECTED.value,
        actor_id=actor_id,
        note=note,
    )


async def reopen(db, timesheet_id, company_id, actor_id=None) -> Timesheet:
    """Move an APPROVED/SUBMITTED timesheet back to DRAFT for correction.

    Clears the prior submit/approve actor stamps so the next submit→approve pass
    is evaluated fresh against the maker-checker rule."""
    ts = await _transition(
        db,
        timesheet_id,
        company_id,
        allowed_from={TimesheetStatus.SUBMITTED.value, TimesheetStatus.APPROVED.value},
        to=TimesheetStatus.DRAFT.value,
        actor_id=actor_id,
    )
    ts.submitted_by_id = None
    ts.approved_by_id = None
    ts.submitted_at = None
    ts.approved_at = None
    await db.commit()
    return await get_detail(db, timesheet_id, company_id)


_LEAVE_OVERLAY = {
    DayStatus.PRESENT.value,
    DayStatus.PAID_LEAVE.value,
    DayStatus.UNPAID_LEAVE.value,
    DayStatus.HALF_DAY.value,
    DayStatus.HALF_DAY_PAID.value,
}


async def resync_leave(db: AsyncSession, company_id: uuid.UUID, timesheet_id: uuid.UUID) -> Timesheet | None:
    """Re-apply the employee's APPROVED leave onto an editable timesheet's grid.

    Only touches DRAFT/REJECTED timesheets whose cycle is still editable (others
    are skipped — the leave stays in the ledger and applies when the sheet is
    regenerated or reopened). Working days that gained leave are stamped; days
    that lost their leave revert to PRESENT. Weekly-offs/holidays and manual
    statuses (e.g. WFH) are left alone. Returns the timesheet, or None if skipped.
    """
    ts = await get_detail(db, timesheet_id, company_id)
    if ts.mode == TimesheetMode.HOURLY.value or ts.status not in _EDITABLE_STATES:
        return None
    cycle = await _load_cycle(db, ts.cycle_id, company_id)
    if cycle.status not in _EDITABLE_CYCLE:
        return None

    leave_days = await leave_service.get_approved_leave_days(
        db, company_id, ts.employee_id, ts.period_start, ts.period_end
    )
    changed = False
    for e in ts.entries:
        if e.day_status not in _LEAVE_OVERLAY:
            continue
        target = leave_days.get(e.entry_date, DayStatus.PRESENT.value)
        if e.day_status != target:
            e.day_status = target
            changed = True
    if changed:
        recompute_aggregates(ts)
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        return await get_detail(db, timesheet_id, company_id)
    return ts


async def resync_leave_for_employee_period(
    db: AsyncSession, company_id: uuid.UUID, employee_id: uuid.UUID, start: date, end: date
) -> int:
    """Resync every editable timesheet of an employee whose period overlaps
    [start, end]. Used after a leave decision. Returns how many were updated."""
    rows = (
        (
            await db.execute(
                select(Timesheet.id).where(
                    Timesheet.company_id == company_id,
                    Timesheet.employee_id == employee_id,
                    Timesheet.deleted_at.is_(None),
                    Timesheet.status.in_(list(_EDITABLE_STATES)),
                    Timesheet.period_start <= end,
                    Timesheet.period_end >= start,
                )
            )
        )
        .scalars()
        .all()
    )
    updated = 0
    for ts_id in rows:
        if await resync_leave(db, company_id, ts_id) is not None:
            updated += 1
    return updated


async def get_approved(db: AsyncSession, employee_id: uuid.UUID, cycle_id: uuid.UUID) -> Timesheet | None:
    """The employee's APPROVED timesheet for the cycle, or None. Consumed by a
    payroll run to source LOP days (attendance) or total hours (hourly)."""
    return (
        await db.execute(
            select(Timesheet).where(
                Timesheet.employee_id == employee_id,
                Timesheet.cycle_id == cycle_id,
                Timesheet.status == TimesheetStatus.APPROVED.value,
                Timesheet.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
