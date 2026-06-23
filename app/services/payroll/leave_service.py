"""Leave management: leave-type config, per-employee balances, and the
leave-request lifecycle.

This is the balance ledger that sits *upstream* of the payroll run: approving a
leave request decrements the employee's balance and (via timesheet_service)
stamps the covered timesheet days as PAID_LEAVE / UNPAID_LEAVE. The run then
reads LOP from the approved timesheet exactly as before — leave never touches
the run directly.

Day counting: a leave spans only *working* days in its range (weekly-offs and
holidays are skipped, mirroring calendar_service). A half-day request covers a
single working day worth 0.5 days.
"""

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll.leave import LeaveBalance, LeaveRequest, LeaveType
from app.payroll.constants import (
    DEFAULT_FINANCIAL_YEAR,
    DEFAULT_LEAVE_TYPES,
    AccrualMethod,
    DayStatus,
    LeaveStatus,
)
from app.services.payroll import calendar_service

_EDITABLE_REQUEST = {LeaveStatus.PENDING.value}


def _balance_left(bal: LeaveBalance) -> Decimal:
    return Decimal(str(bal.accrued or "0")) - Decimal(str(bal.used or "0"))


# ---------------------------------------------------------------------------
# Leave types
# ---------------------------------------------------------------------------
async def list_types(
    db: AsyncSession, company_id: uuid.UUID, *, active_only: bool = False
) -> list[LeaveType]:
    q = select(LeaveType).where(LeaveType.company_id == company_id, LeaveType.deleted_at.is_(None))
    if active_only:
        q = q.where(LeaveType.is_active.is_(True))
    rows = (await db.execute(q.order_by(LeaveType.code))).scalars().all()
    return list(rows)


async def create_type(db: AsyncSession, company_id: uuid.UUID, payload) -> LeaveType:
    lt = LeaveType(
        company_id=company_id,
        name=payload.name.strip(),
        code=payload.code,
        is_paid=payload.is_paid,
        annual_quota=payload.annual_quota,
        accrual=payload.accrual.value,
        carry_forward_cap=payload.carry_forward_cap,
        is_active=payload.is_active,
    )
    db.add(lt)
    try:
        await db.commit()
        await db.refresh(lt)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="A leave type with this code already exists."
        )
    except Exception:
        await db.rollback()
        raise
    return lt


async def seed_default_types(db: AsyncSession, company_id: uuid.UUID) -> list[LeaveType]:
    """Create the standard India-market leave types (constants.DEFAULT_LEAVE_TYPES)
    that the company doesn't already have. Idempotent — matches by code and skips
    existing ones. Returns the rows it created (empty if all already existed)."""
    existing = {t.code for t in await list_types(db, company_id)}
    created: list[LeaveType] = []
    for d in DEFAULT_LEAVE_TYPES:
        if d["code"] in existing:
            continue
        cap = d.get("carry_forward_cap")
        lt = LeaveType(
            company_id=company_id,
            name=d["name"],
            code=d["code"],
            is_paid=d["is_paid"],
            annual_quota=Decimal(str(d["annual_quota"])),
            accrual=d["accrual"],
            carry_forward_cap=(Decimal(str(cap)) if cap is not None else None),
            is_active=True,
        )
        db.add(lt)
        created.append(lt)
    if created:
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        for lt in created:
            await db.refresh(lt)
    return created


async def _get_type(db: AsyncSession, company_id: uuid.UUID, leave_type_id: uuid.UUID) -> LeaveType:
    lt = (
        await db.execute(
            select(LeaveType).where(
                LeaveType.id == leave_type_id,
                LeaveType.company_id == company_id,
                LeaveType.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if not lt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Leave type not found.")
    return lt


async def update_type(
    db: AsyncSession, company_id: uuid.UUID, leave_type_id: uuid.UUID, payload
) -> LeaveType:
    lt = await _get_type(db, company_id, leave_type_id)
    changes = payload.model_dump(exclude_unset=True)
    if "accrual" in changes and changes["accrual"] is not None:
        changes["accrual"] = changes["accrual"].value
    for field, value in changes.items():
        setattr(lt, field, value)
    try:
        await db.commit()
        await db.refresh(lt)
    except Exception:
        await db.rollback()
        raise
    return lt


# ---------------------------------------------------------------------------
# Balances
# ---------------------------------------------------------------------------
def _accrued_for(lt: LeaveType, *, as_of: date | None = None) -> Decimal:
    """How much of the annual quota is credited. ANNUAL -> full quota; MONTHLY ->
    pro-rated by the current month within the financial year (Apr=1 .. Mar=12)."""
    quota = Decimal(str(lt.annual_quota or "0"))
    if lt.accrual != AccrualMethod.MONTHLY.value or as_of is None:
        return quota
    # Indian FY starts in April: month 1 = April .. month 12 = March.
    months_elapsed = ((as_of.month - 4) % 12) + 1
    per_month = (quota / Decimal("12")).quantize(Decimal("0.01"))
    return min(quota, per_month * Decimal(months_elapsed))


async def ensure_balance(
    db: AsyncSession,
    company_id: uuid.UUID,
    employee_id: uuid.UUID,
    lt: LeaveType,
    financial_year: str,
    *,
    as_of: date | None = None,
) -> LeaveBalance:
    """Fetch (or create) the employee's balance for a leave type + FY, refreshing
    the accrued amount to the current accrual point."""
    bal = (
        await db.execute(
            select(LeaveBalance).where(
                LeaveBalance.employee_id == employee_id,
                LeaveBalance.leave_type_id == lt.id,
                LeaveBalance.financial_year == financial_year,
                LeaveBalance.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    accrued = _accrued_for(lt, as_of=as_of)
    if bal is None:
        bal = LeaveBalance(
            company_id=company_id,
            employee_id=employee_id,
            leave_type_id=lt.id,
            financial_year=financial_year,
            entitled=Decimal(str(lt.annual_quota or "0")),
            accrued=accrued,
            used=Decimal("0"),
        )
        db.add(bal)
    else:
        # Keep accrued current (e.g. a MONTHLY type accrues as months pass).
        bal.entitled = Decimal(str(lt.annual_quota or "0"))
        if accrued > Decimal(str(bal.accrued or "0")):
            bal.accrued = accrued
    return bal


async def list_balances(
    db: AsyncSession, company_id: uuid.UUID, financial_year: str, *, employee_id: uuid.UUID | None = None
) -> list[LeaveBalance]:
    """All balances for the FY, seeding a row for every (active employee, active
    paid leave type) pair that doesn't have one yet so the grid is complete."""
    from app.models.enterprise.employee import Employee  # local import avoids a cycle

    types = await list_types(db, company_id, active_only=True)
    emp_q = select(Employee).where(Employee.company_id == company_id, Employee.deleted_at.is_(None))
    if employee_id is not None:
        emp_q = emp_q.where(Employee.id == employee_id)
    employees = (await db.execute(emp_q)).scalars().all()

    today = datetime.utcnow().date()
    for emp in employees:
        for lt in types:
            if not lt.is_paid:
                continue  # unpaid types don't carry a balance
            await ensure_balance(db, company_id, emp.id, lt, financial_year, as_of=today)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    q = select(LeaveBalance).where(
        LeaveBalance.company_id == company_id,
        LeaveBalance.financial_year == financial_year,
        LeaveBalance.deleted_at.is_(None),
    )
    if employee_id is not None:
        q = q.where(LeaveBalance.employee_id == employee_id)
    rows = (await db.execute(q)).scalars().all()
    return list(rows)


# ---------------------------------------------------------------------------
# Leave requests
# ---------------------------------------------------------------------------
async def _leave_working_days(db: AsyncSession, company_id: uuid.UUID, start: date, end: date) -> list[date]:
    """The working days (not weekly-off, not holiday) covered by [start, end]."""
    cfg = await calendar_service.load_calendar_config(db, company_id)
    weekly_offs = set(cfg.weekly_offs)
    holidays = await calendar_service.get_holiday_dates(db, company_id, start, end)
    days: list[date] = []
    day = start
    while day <= end:
        if calendar_service.is_working_day(day, weekly_offs, holidays):
            days.append(day)
        day += timedelta(days=1)
    return days


async def create_request(
    db: AsyncSession, company_id: uuid.UUID, payload, requested_by_id: uuid.UUID | None
) -> LeaveRequest:
    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=400, detail="end_date cannot precede start_date.")
    if payload.half_day and payload.start_date != payload.end_date:
        raise HTTPException(status_code=400, detail="A half-day leave must be a single day.")
    await _get_type(db, company_id, payload.leave_type_id)

    # Block a range that overlaps an existing active (PENDING/APPROVED) request
    # for this employee — otherwise the day is leave twice and an approval would
    # double-decrement the balance while the timesheet stamp silently overwrites.
    clash = (
        await db.execute(
            select(LeaveRequest.id).where(
                LeaveRequest.company_id == company_id,
                LeaveRequest.employee_id == payload.employee_id,
                LeaveRequest.deleted_at.is_(None),
                LeaveRequest.status.in_([LeaveStatus.PENDING.value, LeaveStatus.APPROVED.value]),
                LeaveRequest.start_date <= payload.end_date,
                LeaveRequest.end_date >= payload.start_date,
            )
        )
    ).first()
    if clash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This employee already has a leave request overlapping these dates.",
        )

    working = await _leave_working_days(db, company_id, payload.start_date, payload.end_date)
    if not working:
        raise HTTPException(
            status_code=400, detail="The selected range has no working days (only weekly-offs/holidays)."
        )
    days = Decimal("0.5") if payload.half_day else Decimal(len(working))

    req = LeaveRequest(
        company_id=company_id,
        employee_id=payload.employee_id,
        leave_type_id=payload.leave_type_id,
        start_date=payload.start_date,
        end_date=payload.end_date,
        days=days,
        half_day=payload.half_day,
        status=LeaveStatus.PENDING.value,
        reason=(payload.reason or None),
        requested_by_id=requested_by_id,
    )
    db.add(req)
    try:
        await db.commit()
        await db.refresh(req)
    except Exception:
        await db.rollback()
        raise
    return req


async def list_requests(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    status_filter: str | None = None,
    employee_id: uuid.UUID | None = None,
) -> list[LeaveRequest]:
    q = select(LeaveRequest).where(LeaveRequest.company_id == company_id, LeaveRequest.deleted_at.is_(None))
    if status_filter:
        q = q.where(LeaveRequest.status == status_filter)
    if employee_id is not None:
        q = q.where(LeaveRequest.employee_id == employee_id)
    rows = (await db.execute(q.order_by(LeaveRequest.created_at.desc()))).scalars().all()
    return list(rows)


async def _get_request(db: AsyncSession, company_id: uuid.UUID, request_id: uuid.UUID) -> LeaveRequest:
    req = (
        await db.execute(
            select(LeaveRequest).where(
                LeaveRequest.id == request_id,
                LeaveRequest.company_id == company_id,
                LeaveRequest.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Leave request not found.")
    return req


async def get_request_for_employee(
    db: AsyncSession, company_id: uuid.UUID, request_id: uuid.UUID, employee_id: uuid.UUID
) -> LeaveRequest:
    """Load a leave request only if it belongs to `employee_id` (else 404).
    Used by the self-service endpoints to scope a request to its owner."""
    req = await _get_request(db, company_id, request_id)
    if req.employee_id != employee_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Leave request not found.")
    return req


async def approve_request(
    db: AsyncSession,
    company_id: uuid.UUID,
    request_id: uuid.UUID,
    approver_id: uuid.UUID | None,
    note: str | None = None,
) -> LeaveRequest:
    """Approve a PENDING request: enforce maker-checker, decrement the balance
    (paid types), and mark it APPROVED. The router then stamps timesheets."""
    req = await _get_request(db, company_id, request_id)
    if req.status not in _EDITABLE_REQUEST:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Leave request is {req.status}; only PENDING requests can be approved.",
        )

    cfg = await calendar_service.load_calendar_config(db, company_id)
    if cfg.enforce_maker_checker and approver_id is not None and req.requested_by_id == approver_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "You filed this leave request; a different user must approve it "
                "(segregation of duties is enabled)."
            ),
        )

    lt = await _get_type(db, company_id, req.leave_type_id)
    if lt.is_paid:
        bal = await ensure_balance(
            db, company_id, req.employee_id, lt, DEFAULT_FINANCIAL_YEAR, as_of=datetime.utcnow().date()
        )
        left = _balance_left(bal)
        requested = Decimal(str(req.days))
        if left < requested:
            # Read the values into the message BEFORE rollback — rollback expires
            # the ORM instances, and re-reading them would trigger a sync lazy
            # load (MissingGreenlet) inside this async context.
            detail = f"Insufficient {lt.code} balance: {left} left, {requested} requested."
            await db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
        bal.used = Decimal(str(bal.used or "0")) + requested

    req.status = LeaveStatus.APPROVED.value
    req.approved_by_id = approver_id
    req.decided_at = datetime.utcnow()
    if note is not None:
        req.decision_note = note or None
    try:
        await db.commit()
        await db.refresh(req)
    except Exception:
        await db.rollback()
        raise
    return req


async def _decide_no_balance(
    db: AsyncSession,
    company_id: uuid.UUID,
    request_id: uuid.UUID,
    to: str,
    actor_id: uuid.UUID | None,
    note: str | None,
) -> LeaveRequest:
    req = await _get_request(db, company_id, request_id)
    if req.status not in _EDITABLE_REQUEST:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Leave request is {req.status}; only PENDING requests can change.",
        )
    req.status = to
    req.approved_by_id = actor_id
    req.decided_at = datetime.utcnow()
    if note is not None:
        req.decision_note = note or None
    try:
        await db.commit()
        await db.refresh(req)
    except Exception:
        await db.rollback()
        raise
    return req


async def reject_request(db, company_id, request_id, actor_id=None, note=None) -> LeaveRequest:
    return await _decide_no_balance(db, company_id, request_id, LeaveStatus.REJECTED.value, actor_id, note)


async def cancel_request(db, company_id, request_id, actor_id=None, note=None) -> LeaveRequest:
    """Cancel a PENDING or APPROVED request.

    For an APPROVED paid request this credits the consumed days back to the
    balance; the router then resyncs the covered timesheets so the days revert
    from PAID_LEAVE/UNPAID_LEAVE to PRESENT (the run picks up the lower LOP)."""
    req = await _get_request(db, company_id, request_id)
    if req.status not in {LeaveStatus.PENDING.value, LeaveStatus.APPROVED.value}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Leave request is {req.status}; only PENDING or APPROVED requests can be cancelled.",
        )

    # Reverse the balance only for an APPROVED paid request (PENDING never
    # decremented it). Clamp at zero so a quota change can't drive `used` negative.
    if req.status == LeaveStatus.APPROVED.value:
        lt = await _get_type(db, company_id, req.leave_type_id)
        if lt.is_paid:
            bal = await ensure_balance(
                db, company_id, req.employee_id, lt, DEFAULT_FINANCIAL_YEAR, as_of=datetime.utcnow().date()
            )
            restored = Decimal(str(bal.used or "0")) - Decimal(str(req.days))
            bal.used = max(Decimal("0"), restored)

    req.status = LeaveStatus.CANCELLED.value
    req.approved_by_id = actor_id
    req.decided_at = datetime.utcnow()
    if note is not None:
        req.decision_note = note or None
    try:
        await db.commit()
        await db.refresh(req)
    except Exception:
        await db.rollback()
        raise
    return req


# ---------------------------------------------------------------------------
# Timesheet integration — the leave-day map consumed by timesheet seeding/resync
# ---------------------------------------------------------------------------
async def get_approved_leave_days(
    db: AsyncSession, company_id: uuid.UUID, employee_id: uuid.UUID, start: date, end: date
) -> dict[date, str]:
    """Map each working day in [start, end] covered by an APPROVED leave request
    to a DayStatus value: PAID_LEAVE / UNPAID_LEAVE (full day) or HALF_DAY.

    Consumed by timesheet_service to stamp the daily grid so the payroll run sees
    leave as LOP (unpaid) or paid-time-off (no LOP)."""
    reqs = (
        (
            await db.execute(
                select(LeaveRequest).where(
                    LeaveRequest.company_id == company_id,
                    LeaveRequest.employee_id == employee_id,
                    LeaveRequest.status == LeaveStatus.APPROVED.value,
                    LeaveRequest.deleted_at.is_(None),
                    LeaveRequest.start_date <= end,
                    LeaveRequest.end_date >= start,
                )
            )
        )
        .scalars()
        .all()
    )
    if not reqs:
        return {}

    type_ids = {r.leave_type_id for r in reqs}
    paid = {
        t.id: t.is_paid
        for t in (await db.execute(select(LeaveType).where(LeaveType.id.in_(type_ids)))).scalars().all()
    }

    out: dict[date, str] = {}
    for r in reqs:
        is_paid = paid.get(r.leave_type_id, True)
        if r.half_day:
            d = r.start_date
            if start <= d <= end:
                # Paid half-day: the off-half is paid leave -> no LOP. Unpaid
                # half-day: the off-half is loss-of-pay -> 0.5 LOP.
                out[d] = DayStatus.HALF_DAY_PAID.value if is_paid else DayStatus.HALF_DAY.value
            continue
        lo = max(r.start_date, start)
        hi = min(r.end_date, end)
        full = DayStatus.PAID_LEAVE.value if is_paid else DayStatus.UNPAID_LEAVE.value
        day = lo
        while day <= hi:
            out[day] = full
            day += timedelta(days=1)
    return out
