import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select

from app.models.enterprise.employee import Employee
from app.models.enterprise.user_role import EnterpriseUser as User
from app.models.payroll.leave import LeaveRequest, LeaveType
from app.payroll.constants import DEFAULT_FINANCIAL_YEAR, Permission
from app.payroll.deps import DBSessionDep, get_current_company_id, require_permission
from app.schemas.enterprise.payroll.leave import (
    LeaveBalanceOut,
    LeaveDecisionIn,
    LeaveRequestIn,
    LeaveRequestOut,
    LeaveTypeIn,
    LeaveTypeOut,
    LeaveTypeUpdate,
)
from app.services.payroll import leave_service, timesheet_service

router = APIRouter(prefix="/api/v1/enterprise/leave", tags=["leave"])


async def _employee_names(db, company_id: uuid.UUID) -> dict[uuid.UUID, str]:
    rows = (await db.execute(select(Employee).where(Employee.company_id == company_id))).scalars().all()
    return {e.id: (f"{e.first_name} {e.last_name}".strip() or e.email) for e in rows}


async def _type_map(db, company_id: uuid.UUID) -> dict[uuid.UUID, LeaveType]:
    rows = await leave_service.list_types(db, company_id)
    return {t.id: t for t in rows}


# ---------------------------------------------------------------------------
# Leave types
# ---------------------------------------------------------------------------
@router.get("/types", response_model=list[LeaveTypeOut])
async def list_leave_types(
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> list[LeaveType]:
    # Auto-seed the standard defaults the first time a company has no leave types,
    # so the Apply-for-leave / Leave-types screens are never empty. Idempotent;
    # admins can edit or disable them afterwards.
    types = await leave_service.list_types(db, company_id)
    if not types:
        await leave_service.seed_default_types(db, company_id)
        types = await leave_service.list_types(db, company_id)
    return types


@router.post("/types/seed-defaults", response_model=list[LeaveTypeOut])
async def seed_default_leave_types(
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_CONFIGURE)),
) -> list[LeaveType]:
    """Create the standard set of leave types (CL/SL/EL/ML/PL/BL/LOP) the company
    doesn't already have. Idempotent — returns the rows created this call."""
    return await leave_service.seed_default_types(db, company_id)


@router.post("/types", response_model=LeaveTypeOut, status_code=status.HTTP_201_CREATED)
async def create_leave_type(
    payload: LeaveTypeIn,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_CONFIGURE)),
) -> LeaveType:
    return await leave_service.create_type(db, company_id, payload)


@router.put("/types/{type_id}", response_model=LeaveTypeOut)
async def update_leave_type(
    type_id: uuid.UUID,
    payload: LeaveTypeUpdate,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_CONFIGURE)),
) -> LeaveType:
    return await leave_service.update_type(db, company_id, type_id, payload)


# ---------------------------------------------------------------------------
# Balances
# ---------------------------------------------------------------------------
@router.get("/balances", response_model=list[LeaveBalanceOut])
async def list_balances(
    db: DBSessionDep,
    financial_year: str = Query(default=DEFAULT_FINANCIAL_YEAR),
    employee_id: uuid.UUID | None = Query(default=None),
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> list[LeaveBalanceOut]:
    rows = await leave_service.list_balances(db, company_id, financial_year, employee_id=employee_id)
    emp_names = await _employee_names(db, company_id)
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
                employee_name=emp_names.get(b.employee_id),
                leave_type_name=lt.name if lt else None,
                leave_type_code=lt.code if lt else None,
                is_paid=lt.is_paid if lt else None,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------
def _request_out(req: LeaveRequest, emp_names, types) -> LeaveRequestOut:
    lt = types.get(req.leave_type_id)
    return LeaveRequestOut(
        **LeaveRequestOut.model_validate(req).model_dump(
            exclude={"employee_name", "leave_type_name", "leave_type_code"}
        ),
        employee_name=emp_names.get(req.employee_id),
        leave_type_name=lt.name if lt else None,
        leave_type_code=lt.code if lt else None,
    )


@router.get("/requests", response_model=list[LeaveRequestOut])
async def list_leave_requests(
    db: DBSessionDep,
    status_filter: str | None = Query(default=None, alias="status"),
    employee_id: uuid.UUID | None = Query(default=None),
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> list[LeaveRequestOut]:
    rows = await leave_service.list_requests(
        db, company_id, status_filter=status_filter, employee_id=employee_id
    )
    emp_names = await _employee_names(db, company_id)
    types = await _type_map(db, company_id)
    return [_request_out(r, emp_names, types) for r in rows]


@router.post("/requests", response_model=LeaveRequestOut, status_code=status.HTTP_201_CREATED)
async def create_leave_request(
    payload: LeaveRequestIn,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    current_user: User = Depends(require_permission(Permission.PAYROLL_CONFIGURE)),
) -> LeaveRequestOut:
    req = await leave_service.create_request(db, company_id, payload, current_user.id)
    emp_names = await _employee_names(db, company_id)
    types = await _type_map(db, company_id)
    return _request_out(req, emp_names, types)


@router.post("/requests/{request_id}/approve", response_model=LeaveRequestOut)
async def approve_leave_request(
    request_id: uuid.UUID,
    payload: LeaveDecisionIn,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    current_user: User = Depends(require_permission(Permission.PAYROLL_APPROVE)),
) -> LeaveRequestOut:
    req = await leave_service.approve_request(db, company_id, request_id, current_user.id, payload.note)
    # Stamp the approved leave onto any editable timesheet covering the dates so
    # a subsequent payroll run reflects it as LOP / paid time off.
    await timesheet_service.resync_leave_for_employee_period(
        db, company_id, req.employee_id, req.start_date, req.end_date
    )
    # resync commits (expiring `req`); reload it before serialising.
    await db.refresh(req)
    emp_names = await _employee_names(db, company_id)
    types = await _type_map(db, company_id)
    return _request_out(req, emp_names, types)


@router.post("/requests/{request_id}/reject", response_model=LeaveRequestOut)
async def reject_leave_request(
    request_id: uuid.UUID,
    payload: LeaveDecisionIn,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    current_user: User = Depends(require_permission(Permission.PAYROLL_APPROVE)),
) -> LeaveRequestOut:
    req = await leave_service.reject_request(db, company_id, request_id, current_user.id, payload.note)
    emp_names = await _employee_names(db, company_id)
    types = await _type_map(db, company_id)
    return _request_out(req, emp_names, types)


@router.post("/requests/{request_id}/cancel", response_model=LeaveRequestOut)
async def cancel_leave_request(
    request_id: uuid.UUID,
    payload: LeaveDecisionIn,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    current_user: User = Depends(require_permission(Permission.PAYROLL_CONFIGURE)),
) -> LeaveRequestOut:
    req = await leave_service.cancel_request(db, company_id, request_id, current_user.id, payload.note)
    # Drop the (now-cancelled) leave from any editable timesheet covering the
    # dates so the days revert to PRESENT and a subsequent run reflects it.
    await timesheet_service.resync_leave_for_employee_period(
        db, company_id, req.employee_id, req.start_date, req.end_date
    )
    # resync commits (expiring `req`); reload it before serialising.
    await db.refresh(req)
    emp_names = await _employee_names(db, company_id)
    types = await _type_map(db, company_id)
    return _request_out(req, emp_names, types)
