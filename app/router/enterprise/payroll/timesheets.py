import csv
import io
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select

from app.models.enterprise.employee import Employee
from app.models.enterprise.user_role import EnterpriseUser as User
from app.models.payroll.timesheets import Timesheet
from app.payroll.constants import Permission
from app.payroll.deps import DBSessionDep, get_current_company_id, require_permission
from app.schemas.enterprise.payroll.timesheets import (
    AttendanceImportResult,
    TimesheetBulkEntryUpdate,
    TimesheetDetailOut,
    TimesheetGenerateResult,
    TimesheetOut,
    TimesheetRejectIn,
    TimesheetSummaryOut,
)
from app.services.payroll import timesheet_service

router = APIRouter(prefix="/api/v1/enterprise/timesheets", tags=["timesheets"])

# Largest attendance CSV we'll accept (1 MB ≈ tens of thousands of rows).
_MAX_IMPORT_BYTES = 1_000_000

# CSV header (lowercased) -> canonical field the import service reads.
_HEADER_ALIASES = {
    "employee_code": "employee_code",
    "code": "employee_code",
    "emp_code": "employee_code",
    "employee": "employee_code",
    "employee_id": "employee_id",
    "empid": "employee_id",
    "date": "date",
    "attendance_date": "date",
    "day": "date",
    "status": "status",
    "attendance": "status",
    "hours": "hours",
    "worked_hours": "hours",
    "total_hours": "hours",
    "check_in": "check_in",
    "in": "check_in",
    "in_time": "check_in",
    "intime": "check_in",
    "punch_in": "check_in",
    "check_out": "check_out",
    "out": "check_out",
    "out_time": "check_out",
    "outtime": "check_out",
    "punch_out": "check_out",
}


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


@router.post("/cycles/{cycle_id}/import", response_model=AttendanceImportResult)
async def import_cycle_attendance(
    cycle_id: uuid.UUID,
    db: DBSessionDep,
    file: UploadFile = File(...),
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_CONFIGURE)),
) -> AttendanceImportResult:
    """Import a CSV attendance export (e.g. from a biometric device) onto the
    cycle's timesheets. Columns (header row, case-insensitive): employee_code,
    date (YYYY-MM-DD), and any of status / hours / check_in / check_out. Rows that
    can't be applied are reported, not fatal. Generate the cycle's timesheets
    first — rows for employees without one are skipped."""
    raw = await file.read()
    if len(raw) > _MAX_IMPORT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Attendance file is too large (max 1 MB).",
        )
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The file is empty or has no header row."
        )
    rows: list[dict] = []
    for raw_row in reader:
        norm: dict[str, str] = {}
        for header, value in raw_row.items():
            canonical = _HEADER_ALIASES.get((header or "").strip().lower())
            if canonical:
                norm[canonical] = (value or "").strip()
        rows.append(norm)
    result = await timesheet_service.import_attendance(db, company_id, cycle_id, rows)
    return AttendanceImportResult(**result)


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
