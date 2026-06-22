import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select

from app.models.enterprise.company import Company
from app.payroll.constants import PayrollCycleStatus, Permission
from app.payroll.deps import DBSessionDep, get_current_company_id, require_permission
from app.services.payroll import payroll_service, report_service

router = APIRouter(prefix="/api/v1/enterprise/reports", tags=["reports"])

ReportFormat = Literal["csv", "pdf"]

_MEDIA = {"csv": "text/csv", "pdf": "application/pdf"}


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name) or "report"


def _file_response(content: bytes, fmt: ReportFormat, filename_base: str) -> Response:
    return Response(
        content=content,
        media_type=_MEDIA[fmt],
        headers={"Content-Disposition": f'attachment; filename="{_safe(filename_base)}.{fmt}"'},
    )


async def _company_name(db: DBSessionDep, company_id: uuid.UUID) -> str:
    company = (await db.execute(select(Company).where(Company.id == company_id))).scalar_one_or_none()
    return company.name if company else "Company"


@router.get("/salary-register")
async def salary_register(
    cycle_id: uuid.UUID,
    db: DBSessionDep,
    format: ReportFormat = "csv",
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> Response:
    """Per-employee salary register for a cycle (CSV or PDF). Internal HR report
    — available once the cycle has been run (has payslips), not gated on PAID."""
    cycle = await payroll_service._load_cycle(db, cycle_id, company_id)
    if cycle.status == PayrollCycleStatus.DRAFT.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Run the cycle first — there are no payslips to report yet.",
        )
    records = await report_service.salary_register_records(db, company_id, cycle)
    base = f"salary-register-{cycle.name}"
    if format == "csv":
        content = report_service.records_to_csv(report_service.SALARY_REGISTER_COLUMNS, records)
    else:
        company_name = await _company_name(db, company_id)
        content = await run_in_threadpool(report_service.salary_register_pdf, company_name, cycle, records)
    return _file_response(content, format, base)


@router.get("/payroll-summary")
async def payroll_summary(
    db: DBSessionDep,
    format: ReportFormat = "csv",
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> Response:
    """Cycle-level payroll summary across all cycles (CSV or PDF)."""
    records = await report_service.payroll_summary_records(db, company_id)
    if format == "csv":
        content = report_service.records_to_csv(report_service.PAYROLL_SUMMARY_COLUMNS, records)
    else:
        company_name = await _company_name(db, company_id)
        content = await run_in_threadpool(report_service.payroll_summary_pdf, company_name, records)
    return _file_response(content, format, "payroll-summary")
