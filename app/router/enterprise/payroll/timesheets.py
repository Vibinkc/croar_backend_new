import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.models.enterprise.employee import Employee
from app.models.enterprise.user_role import EnterpriseUser as User
from app.models.payroll.timesheets import Timesheet
from app.payroll.constants import Permission
from app.payroll.deps import DBSessionDep, get_current_company_id, require_permission
from app.schemas.enterprise.payroll.timesheets import (
    TimesheetBulkEntryUpdate,
    TimesheetDetailOut,
    TimesheetGenerateResult,
    TimesheetOut,
    TimesheetRejectIn,
    TimesheetSummaryOut,
)
from app.services.payroll import timesheet_service

router = APIRouter(prefix="/api/v1/enterprise/timesheets", tags=["timesheets"])


def _employee_label(emp: Employee | None) -> tuple[str | None, str | None]:
    if emp is None:
        return None, None
    name = f"{emp.first_name} {emp.last_name}".strip() or emp.email
    return name, emp.employee_id


@router.post("/cycles/{cycle_id}/generate", response_model=TimesheetGenerateResult)
async def generate_timesheets(
    cycle_id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_CONFIGURE)),
) -> TimesheetGenerateResult:
    """Seed a timesheet (with daily entries) for every payable employee in the
    cycle. Idempotent — existing timesheets are left untouched."""
    result = await timesheet_service.generate_for_cycle(db, company_id, cycle_id)
    return TimesheetGenerateResult(**result)


@router.get("/cycles/{cycle_id}", response_model=list[TimesheetSummaryOut])
async def list_cycle_timesheets(
    cycle_id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> list[TimesheetSummaryOut]:
    rows = await timesheet_service.list_for_cycle(db, company_id, cycle_id)
    emps = {
        e.id: e
        for e in (await db.execute(select(Employee).where(Employee.company_id == company_id))).scalars().all()
    }
    out: list[TimesheetSummaryOut] = []
    for ts in rows:
        name, code = _employee_label(emps.get(ts.employee_id))
        out.append(
            TimesheetSummaryOut(
                **TimesheetOut.model_validate(ts).model_dump(), employee_name=name, employee_code=code
            )
        )
    return out


@router.get("/{timesheet_id}", response_model=TimesheetDetailOut)
async def get_timesheet(
    timesheet_id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> TimesheetDetailOut:
    ts = await timesheet_service.get_detail(db, timesheet_id, company_id)
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


@router.put("/{timesheet_id}/entries", response_model=TimesheetDetailOut)
async def update_entries(
    timesheet_id: uuid.UUID,
    payload: TimesheetBulkEntryUpdate,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_CONFIGURE)),
) -> TimesheetDetailOut:
    ts = await timesheet_service.bulk_update_entries(db, timesheet_id, company_id, payload.entries)
    return TimesheetDetailOut.model_validate(ts, from_attributes=True)


@router.post("/{timesheet_id}/submit", response_model=TimesheetOut)
async def submit_timesheet(
    timesheet_id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    current_user: User = Depends(require_permission(Permission.PAYROLL_CONFIGURE)),
) -> Timesheet:
    return await timesheet_service.submit(db, timesheet_id, company_id, current_user.id)


@router.post("/{timesheet_id}/approve", response_model=TimesheetOut)
async def approve_timesheet(
    timesheet_id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    current_user: User = Depends(require_permission(Permission.PAYROLL_APPROVE)),
) -> Timesheet:
    return await timesheet_service.approve(db, timesheet_id, company_id, current_user.id)


@router.post("/{timesheet_id}/reject", response_model=TimesheetOut)
async def reject_timesheet(
    timesheet_id: uuid.UUID,
    payload: TimesheetRejectIn,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    current_user: User = Depends(require_permission(Permission.PAYROLL_APPROVE)),
) -> Timesheet:
    return await timesheet_service.reject(db, timesheet_id, company_id, payload.note, current_user.id)


@router.post("/{timesheet_id}/reopen", response_model=TimesheetOut)
async def reopen_timesheet(
    timesheet_id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    current_user: User = Depends(require_permission(Permission.PAYROLL_APPROVE)),
) -> Timesheet:
    return await timesheet_service.reopen(db, timesheet_id, company_id, current_user.id)
