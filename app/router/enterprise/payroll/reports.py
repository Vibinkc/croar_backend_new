import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select

from app.models.enterprise.company import Company
from app.payroll.constants import DEFAULT_FINANCIAL_YEAR, PayrollCycleStatus, Permission
from app.payroll.deps import DBSessionDep, get_current_company_id, require_permission
from app.services.payroll import payroll_service, report_service, reports_extra

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
    # A non-DRAFT cycle can still have no payslips (e.g. cancelled before it was
    # ever run, or a run where every employee was skipped) — don't emit an empty
    # register file.
    if not records:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This cycle has no payslips to report."
        )
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


# ---------------------------------------------------------------------------
# The reports that answer "why didn't this person get paid?"
#
# Everything below is a query over existing tables — no new models. Each follows
# the same shape as the two above: build records, serialise to CSV or PDF, send
# it as a download.
# ---------------------------------------------------------------------------
@router.get("/missing-information")
async def missing_information(
    db: DBSessionDep,
    format: ReportFormat = "csv",
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> Response:
    """Employees with incomplete records, blocking ones first.

    Run this *before* a cycle, not after. An employee with no salary structure
    is skipped silently by the run; one with no bank account gets a payslip and
    no money, which is harder to notice.
    """
    records = await reports_extra.missing_information_records(db, company_id)
    if format == "csv":
        content = report_service.records_to_csv(reports_extra.MISSING_INFO_COLUMNS, records)
    else:
        content = await run_in_threadpool(
            reports_extra.missing_information_pdf, await _company_name(db, company_id), records
        )
    return _file_response(content, format, "missing-information")


@router.get("/skipped-summary")
async def skipped_summary(
    cycle_id: uuid.UUID,
    db: DBSessionDep,
    format: ReportFormat = "csv",
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> Response:
    """Who a given run left out, and the reason recorded at the time."""
    cycle = await payroll_service._load_cycle(db, cycle_id, company_id)
    if cycle.status == PayrollCycleStatus.DRAFT.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This cycle has not been run, so nobody has been skipped yet.",
        )
    records = reports_extra.skipped_summary_records(cycle)
    if format == "csv":
        content = report_service.records_to_csv(reports_extra.SKIPPED_SUMMARY_COLUMNS, records)
    else:
        content = await run_in_threadpool(
            reports_extra.skipped_summary_pdf, await _company_name(db, company_id), cycle, records
        )
    return _file_response(content, format, f"skipped-summary-{cycle.name}")


@router.get("/variance")
async def variance(
    from_cycle_id: uuid.UUID,
    to_cycle_id: uuid.UUID,
    db: DBSessionDep,
    format: ReportFormat = "csv",
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> Response:
    """What moved between two cycles, per employee, largest movement first."""
    if from_cycle_id == to_cycle_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Pick two different cycles to compare."
        )
    from_cycle = await payroll_service._load_cycle(db, from_cycle_id, company_id)
    to_cycle = await payroll_service._load_cycle(db, to_cycle_id, company_id)
    records = await reports_extra.variance_records(db, company_id, from_cycle, to_cycle)
    if format == "csv":
        content = report_service.records_to_csv(reports_extra.VARIANCE_COLUMNS, records)
    else:
        content = await run_in_threadpool(
            reports_extra.variance_pdf, await _company_name(db, company_id), from_cycle, to_cycle, records
        )
    return _file_response(content, format, f"variance-{from_cycle.name}-to-{to_cycle.name}")


@router.get("/master-ctc")
async def master_ctc(
    db: DBSessionDep,
    format: ReportFormat = "csv",
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> Response:
    """Current salary package per employee, with the statutory toggles on it."""
    records = await reports_extra.master_ctc_records(db, company_id)
    if format == "csv":
        content = report_service.records_to_csv(reports_extra.MASTER_CTC_COLUMNS, records)
    else:
        content = await run_in_threadpool(
            reports_extra.master_ctc_pdf, await _company_name(db, company_id), records
        )
    return _file_response(content, format, "master-ctc")


@router.get("/hr-register")
async def hr_register(
    db: DBSessionDep,
    format: ReportFormat = "csv",
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> Response:
    """The employee master, statutory identifiers included."""
    records = await reports_extra.hr_register_records(db, company_id)
    if format == "csv":
        content = report_service.records_to_csv(reports_extra.HR_REGISTER_COLUMNS, records)
    else:
        content = await run_in_threadpool(
            reports_extra.hr_register_pdf, await _company_name(db, company_id), records
        )
    return _file_response(content, format, "hr-register")


@router.get("/tax-computation")
async def tax_computation(
    db: DBSessionDep,
    format: ReportFormat = "csv",
    financial_year: str = DEFAULT_FINANCIAL_YEAR,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> Response:
    """Year-to-date earnings and tax against each employee's declaration."""
    records = await reports_extra.tax_computation_records(db, company_id, financial_year)
    if format == "csv":
        content = report_service.records_to_csv(reports_extra.TAX_COMPUTATION_COLUMNS, records)
    else:
        content = await run_in_threadpool(
            reports_extra.tax_computation_pdf, await _company_name(db, company_id), financial_year, records
        )
    return _file_response(content, format, f"tax-computation-{financial_year}")


@router.get("/tds")
async def tds_reconciliation(
    db: DBSessionDep,
    format: ReportFormat = "csv",
    financial_year: str = DEFAULT_FINANCIAL_YEAR,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> Response:
    """Tax withheld from payslips against challans recorded as deposited."""
    records = await reports_extra.tds_records(db, company_id, financial_year)
    if format == "csv":
        content = report_service.records_to_csv(reports_extra.TDS_COLUMNS, records)
    else:
        content = await run_in_threadpool(
            reports_extra.tds_pdf, await _company_name(db, company_id), financial_year, records
        )
    return _file_response(content, format, f"tds-{financial_year}")
