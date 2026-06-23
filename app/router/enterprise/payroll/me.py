"""Employee self-service endpoints (/api/v1/me).

Scoped to the signed-in user's own linked Employee record (see
`get_current_employee_id`). These are deliberately separate from the company-wide
`/enterprise/*` routes: an EMPLOYEE-role user holds only `self:read` and can
reach nothing here that isn't their own.

Read: own timesheets, own released payslips, own leave balances/history.
Write: file (and cancel) one's own leave request — the employee_id is taken from
the link, never the payload, so a user can only ever act on themselves.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.models.enterprise.employee import Employee
from app.models.enterprise.user_role import EnterpriseUser as User
from app.models.payroll import PayrollCycle, Payslip
from app.models.payroll.leave import LeaveType
from app.payroll.constants import DEFAULT_FINANCIAL_YEAR, PayrollCycleStatus, Permission
from app.payroll.deps import DBSessionDep, get_current_company_id, get_current_employee_id, require_permission
from app.schemas.enterprise.payroll.leave import (
    LeaveBalanceOut,
    LeaveDecisionIn,
    LeaveRequestIn,
    LeaveRequestOut,
    LeaveTypeOut,
    MyLeaveRequestIn,
)
from app.schemas.enterprise.payroll.payroll import MyPayslipOut
from app.schemas.enterprise.payroll.timesheets import (
    TimesheetBulkEntryUpdate,
    TimesheetDetailOut,
    TimesheetOut,
    TimesheetSummaryOut,
)
from app.services.payroll import leave_service, timesheet_service

router = APIRouter(prefix="/api/v1/me", tags=["self-service"])


def _employee_label(emp: Employee | None) -> tuple[str | None, str | None]:
    if emp is None:
        return None, None
    name = f"{emp.first_name} {emp.last_name}".strip() or emp.email
    return name, emp.employee_id


# ---------------------------------------------------------------------------
# Timesheets
# ---------------------------------------------------------------------------
@router.get("/timesheets", response_model=list[TimesheetSummaryOut])
async def my_timesheets(
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    employee_id: uuid.UUID = Depends(get_current_employee_id),
) -> list[TimesheetSummaryOut]:
    """List the signed-in employee's own timesheets (all cycles)."""
    rows = await timesheet_service.list_for_employee(db, company_id, employee_id)
    emp = (await db.execute(select(Employee).where(Employee.id == employee_id))).scalar_one_or_none()
    name, code = _employee_label(emp)
    return [
        TimesheetSummaryOut(
            **TimesheetOut.model_validate(ts).model_dump(), employee_name=name, employee_code=code
        )
        for ts in rows
    ]


async def _timesheet_detail_out(db: DBSessionDep, ts) -> TimesheetDetailOut:
    """Build the detail response (employee + actor display names) for a timesheet."""
    emp = (await db.execute(select(Employee).where(Employee.id == ts.employee_id))).scalar_one_or_none()
    name, code = _employee_label(emp)
    actor_ids = [i for i in (ts.submitted_by_id, ts.approved_by_id) if i is not None]
    users = (
        {
            u.id: (u.full_name or u.email)
            for u in (await db.execute(select(User).where(User.id.in_(actor_ids)))).scalars().all()
        }
        if actor_ids
        else {}
    )
    out = TimesheetDetailOut.model_validate(ts, from_attributes=True)
    return out.model_copy(
        update={
            "employee_name": name,
            "employee_code": code,
            "submitted_by_name": users.get(ts.submitted_by_id),
            "approved_by_name": users.get(ts.approved_by_id),
        }
    )


@router.get("/timesheets/{timesheet_id}", response_model=TimesheetDetailOut)
async def my_timesheet_detail(
    timesheet_id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    employee_id: uuid.UUID = Depends(get_current_employee_id),
) -> TimesheetDetailOut:
    """View one of the signed-in employee's own timesheets (404 if not theirs)."""
    ts = await timesheet_service.get_owned_detail(db, timesheet_id, company_id, employee_id)
    return await _timesheet_detail_out(db, ts)


@router.put("/timesheets/{timesheet_id}/mark", response_model=TimesheetDetailOut)
async def mark_my_attendance(
    timesheet_id: uuid.UUID,
    payload: TimesheetBulkEntryUpdate,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    employee_id: uuid.UUID = Depends(get_current_employee_id),
) -> TimesheetDetailOut:
    """Self-mark attendance on one's own draft timesheet (Present/WFH + hours).
    Guarded server-side: own sheet only, editable status, no future dates, no
    self-LOP, and leave days are protected. HR still submits/approves."""
    ts = await timesheet_service.self_mark_entries(
        db, timesheet_id, company_id, employee_id, payload.entries, today=datetime.utcnow().date()
    )
    return await _timesheet_detail_out(db, ts)


# ---------------------------------------------------------------------------
# Payslips — only RELEASED (cycle PAID) payslips are visible, mirroring the
# enterprise PAID gate on get_payslip.
# ---------------------------------------------------------------------------
def _my_payslip_out(payslip: Payslip, cycle: PayrollCycle) -> MyPayslipOut:
    return MyPayslipOut.model_validate(payslip, from_attributes=True).model_copy(
        update={
            "cycle_name": cycle.name,
            "period_start": cycle.period_start,
            "period_end": cycle.period_end,
            "pay_date": cycle.pay_date,
        }
    )


@router.get("/payslips", response_model=list[MyPayslipOut])
async def my_payslips(
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    employee_id: uuid.UUID = Depends(get_current_employee_id),
) -> list[MyPayslipOut]:
    """The signed-in employee's released payslips (cycle PAID), newest first."""
    rows = (
        await db.execute(
            select(Payslip, PayrollCycle)
            .join(PayrollCycle, Payslip.cycle_id == PayrollCycle.id)
            .where(
                Payslip.company_id == company_id,
                Payslip.employee_id == employee_id,
                PayrollCycle.status == PayrollCycleStatus.PAID.value,
                PayrollCycle.deleted_at.is_(None),
            )
            .order_by(PayrollCycle.period_start.desc())
        )
    ).all()
    return [_my_payslip_out(ps, cyc) for ps, cyc in rows]


@router.get("/payslips/{payslip_id}", response_model=MyPayslipOut)
async def my_payslip_detail(
    payslip_id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    employee_id: uuid.UUID = Depends(get_current_employee_id),
) -> MyPayslipOut:
    """View one of the employee's own released payslips (404 if not theirs/unpaid)."""
    row = (
        await db.execute(
            select(Payslip, PayrollCycle)
            .join(PayrollCycle, Payslip.cycle_id == PayrollCycle.id)
            .where(
                Payslip.id == payslip_id,
                Payslip.company_id == company_id,
                Payslip.employee_id == employee_id,
                PayrollCycle.status == PayrollCycleStatus.PAID.value,
                PayrollCycle.deleted_at.is_(None),
            )
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payslip not found.")
    return _my_payslip_out(row[0], row[1])


# ---------------------------------------------------------------------------
# Leave — own balances, history, and self-service apply/cancel
# ---------------------------------------------------------------------------
async def _type_map(db: DBSessionDep, company_id: uuid.UUID) -> dict[uuid.UUID, LeaveType]:
    rows = await leave_service.list_types(db, company_id)
    return {t.id: t for t in rows}


def _request_out(req, types: dict[uuid.UUID, LeaveType], emp_name: str | None) -> LeaveRequestOut:
    lt = types.get(req.leave_type_id)
    return LeaveRequestOut(
        **LeaveRequestOut.model_validate(req).model_dump(
            exclude={"employee_name", "leave_type_name", "leave_type_code"}
        ),
        employee_name=emp_name,
        leave_type_name=lt.name if lt else None,
        leave_type_code=lt.code if lt else None,
    )


@router.get("/leave/types", response_model=list[LeaveTypeOut])
async def my_leave_types(
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: uuid.UUID = Depends(get_current_employee_id),
) -> list[LeaveType]:
    """Active leave types — so the apply form can offer the right options.

    If the company has no leave types yet, seed the standard defaults
    (CL/SL/EL/ML/PL/BL/LOP) so an employee always has something to request;
    admins can edit/disable them afterwards in Payroll → Leave.
    """
    types = await leave_service.list_types(db, company_id, active_only=True)
    if not types:
        await leave_service.seed_default_types(db, company_id)
        types = await leave_service.list_types(db, company_id, active_only=True)
    return types


@router.get("/leave/balances", response_model=list[LeaveBalanceOut])
async def my_leave_balances(
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    employee_id: uuid.UUID = Depends(get_current_employee_id),
) -> list[LeaveBalanceOut]:
    rows = await leave_service.list_balances(db, company_id, DEFAULT_FINANCIAL_YEAR, employee_id=employee_id)
    types = await _type_map(db, company_id)
    out: list[LeaveBalanceOut] = []
    for b in rows:
        lt = types.get(b.leave_type_id)
        out.append(
            LeaveBalanceOut(
                **LeaveBalanceOut.model_validate(b).model_dump(
                    exclude={"balance", "employee_name", "leave_type_name", "leave_type_code", "is_paid"}
                ),
                balance=Decimal(str(b.accrued or "0")) - Decimal(str(b.used or "0")),
                leave_type_name=lt.name if lt else None,
                leave_type_code=lt.code if lt else None,
                is_paid=lt.is_paid if lt else None,
            )
        )
    return out


@router.get("/leave/requests", response_model=list[LeaveRequestOut])
async def my_leave_requests(
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    employee_id: uuid.UUID = Depends(get_current_employee_id),
) -> list[LeaveRequestOut]:
    rows = await leave_service.list_requests(db, company_id, employee_id=employee_id)
    types = await _type_map(db, company_id)
    emp = (await db.execute(select(Employee).where(Employee.id == employee_id))).scalar_one_or_none()
    name, _ = _employee_label(emp)
    return [_request_out(r, types, name) for r in rows]


@router.post("/leave/requests", response_model=LeaveRequestOut, status_code=201)
async def file_my_leave_request(
    payload: MyLeaveRequestIn,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    employee_id: uuid.UUID = Depends(get_current_employee_id),
    current_user: User = Depends(require_permission(Permission.SELF_READ)),
) -> LeaveRequestOut:
    """File a leave request for oneself. employee_id is forced from the link, so
    a user can never file leave on another employee's behalf."""
    # Read user fields BEFORE the service commit — the commit expires the ORM
    # instance and a later attribute access would trigger a sync lazy-load
    # (MissingGreenlet) in this async context.
    actor_id = current_user.id
    actor_name = current_user.full_name or current_user.email
    full = LeaveRequestIn(
        employee_id=employee_id,
        leave_type_id=payload.leave_type_id,
        start_date=payload.start_date,
        end_date=payload.end_date,
        half_day=payload.half_day,
        reason=payload.reason,
    )
    req = await leave_service.create_request(db, company_id, full, actor_id)
    types = await _type_map(db, company_id)
    return _request_out(req, types, actor_name)


@router.post("/leave/requests/{request_id}/cancel", response_model=LeaveRequestOut)
async def cancel_my_leave_request(
    request_id: uuid.UUID,
    payload: LeaveDecisionIn,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    employee_id: uuid.UUID = Depends(get_current_employee_id),
    current_user: User = Depends(require_permission(Permission.SELF_READ)),
) -> LeaveRequestOut:
    """Cancel one's own leave request (404 if not theirs). Restores balance and
    resyncs the timesheet exactly like the HR-side cancel."""
    # Capture user fields before the service commit (see file_my_leave_request).
    actor_id = current_user.id
    actor_name = current_user.full_name or current_user.email
    await leave_service.get_request_for_employee(db, company_id, request_id, employee_id)
    req = await leave_service.cancel_request(db, company_id, request_id, actor_id, payload.note)
    await timesheet_service.resync_leave_for_employee_period(
        db, company_id, req.employee_id, req.start_date, req.end_date
    )
    await db.refresh(req)
    types = await _type_map(db, company_id)
    return _request_out(req, types, actor_name)
