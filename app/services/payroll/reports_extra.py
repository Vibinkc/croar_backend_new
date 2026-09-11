"""The reports a payroll product needs beyond a salary register.

Croar shipped two reports: the salary register and the payroll summary. Both
answer "what did we pay?". None of them answered the question that actually
costs money — "why didn't this person get paid?" — which is the one asked days
after a run, when the run's response body is long gone.

Everything here is a query over tables that already exist. No new models, no new
migration. Serialisation (CSV/PDF) is reused from ``report_service`` so a new
report is a column list plus a record builder.

The set mirrors what a mature payroll product exposes:
  missing-information  employees who cannot be paid, and what is missing
  skipped-summary      who a given run left out, and why
  variance             what moved between two cycles, per employee
  master-ctc           the current salary package for everyone
  hr-register          the employee master, statutory identifiers included
  tax-computation      each employee's annual tax position for a financial year
  tds                  tax deducted against tax deposited, month by month
"""

import uuid
from collections import defaultdict
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enterprise.employee import Employee
from app.models.payroll import PayrollCycle, Payslip, SalaryStructure
from app.models.payroll.taxes import EmployeeTaxProfile, TdsChallan
from app.payroll.constants import DEFAULT_FINANCIAL_YEAR
from app.services.payroll.report_service import Column, _table_pdf

# ---------------------------------------------------------------------------
# Column definitions
# ---------------------------------------------------------------------------
MISSING_INFO_COLUMNS: list[Column] = [
    ("Employee Code", "code", False),
    ("Employee Name", "name", False),
    ("Email", "email", False),
    ("Status", "employment_status", False),
    ("Blocking", "blocking", False),
    ("Missing", "missing", False),
]

SKIPPED_SUMMARY_COLUMNS: list[Column] = [
    ("Employee Name", "name", False),
    ("Employee ID", "employee_id", False),
    ("Reason", "reason", False),
]

VARIANCE_COLUMNS: list[Column] = [
    ("Employee Code", "code", False),
    ("Employee Name", "name", False),
    ("Gross (from)", "gross_from", True),
    ("Gross (to)", "gross_to", True),
    ("Gross Change", "gross_delta", True),
    ("Net (from)", "net_from", True),
    ("Net (to)", "net_to", True),
    ("Net Change", "net_delta", True),
    ("Change %", "net_delta_pct", True),
    ("Movement", "movement", False),
]

MASTER_CTC_COLUMNS: list[Column] = [
    ("Employee Code", "code", False),
    ("Employee Name", "name", False),
    ("Designation", "designation", False),
    ("CTC", "ctc", True),
    ("Currency", "currency", False),
    ("Frequency", "pay_frequency", False),
    ("Monthly Gross", "monthly_gross", True),
    ("Effective From", "effective_from", False),
    ("PF", "pf", False),
    ("ESI", "esi", False),
    ("PT", "pt", False),
    ("TDS", "tds", False),
    ("Template", "template", False),
]

HR_REGISTER_COLUMNS: list[Column] = [
    ("Employee Code", "code", False),
    ("Employee Name", "name", False),
    ("Email", "email", False),
    ("Mobile", "mobile", False),
    ("Designation", "designation", False),
    ("Employment Type", "employment_type", False),
    ("Status", "employment_status", False),
    ("Date of Joining", "date_of_joining", False),
    ("Probation End", "probation_end", False),
    ("Notice Period (days)", "notice_period", True),
    ("PAN", "pan", False),
    ("Aadhaar", "aadhaar", False),
    ("UAN", "uan", False),
    ("ESIC", "esic", False),
    ("Bank Account", "bank_account", False),
    ("State", "state", False),
]

TAX_COMPUTATION_COLUMNS: list[Column] = [
    ("Employee Code", "code", False),
    ("Employee Name", "name", False),
    ("PAN", "pan", False),
    ("Regime", "regime", False),
    ("Gross Paid (YTD)", "gross_ytd", True),
    ("Previous Employer", "prev_income", True),
    ("Total Income", "total_income", True),
    ("80C", "declared_80c", True),
    ("80D", "declared_80d", True),
    ("HRA Rent", "declared_hra_rent", True),
    ("Home Loan Interest", "declared_home_loan", True),
    ("Other", "declared_other", True),
    ("Total Declared", "total_declared", True),
    ("TDS Deducted (YTD)", "tds_ytd", True),
    ("Previous Employer TDS", "prev_tds", True),
    ("Declaration", "declaration_state", False),
]

TDS_COLUMNS: list[Column] = [
    ("Month", "period_month", False),
    ("TDS Deducted", "deducted", True),
    ("TDS Deposited", "deposited", True),
    ("Difference", "difference", True),
    ("Challan Number", "challan_number", False),
    ("BSR Code", "bsr_code", False),
    ("Deposit Date", "deposit_date", False),
    ("Interest", "interest", True),
    ("Penalty", "penalty", True),
    ("Status", "reconciliation", False),
]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _name(e: Employee) -> str:
    return f"{e.first_name} {e.last_name}".strip()


def _active_employees(company_id: uuid.UUID) -> Any:
    return select(Employee).where(Employee.company_id == company_id, Employee.deleted_at.is_(None))


async def _structures_by_employee(
    db: AsyncSession, company_id: uuid.UUID
) -> dict[uuid.UUID, SalaryStructure]:
    rows = (
        (
            await db.execute(
                select(SalaryStructure).where(
                    SalaryStructure.company_id == company_id,
                    SalaryStructure.is_active.is_(True),
                    SalaryStructure.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    return {s.employee_id: s for s in rows}


def _statutory_amount(payslip: Payslip, *codes: str) -> Decimal:
    """Sum the named deduction codes off a payslip. Deduction lines are a JSONB
    snapshot of [{code,label,amount}], so this reads history as it was run, not
    as the structure is configured today."""
    wanted = {c.upper() for c in codes}
    total = Decimal("0")
    for line in payslip.deductions or []:
        if str(line.get("code", "")).upper() in wanted:
            total += Decimal(str(line.get("amount", 0) or 0))
    return total


# ---------------------------------------------------------------------------
# 1. Missing Information — who cannot be paid, and what is missing
# ---------------------------------------------------------------------------
async def missing_information_records(db: AsyncSession, company_id: uuid.UUID) -> list[dict[str, Any]]:
    """Every employee with something missing, and whether it blocks payment.

    "Blocking" is the distinction that matters. Without a salary structure the
    run cannot produce a payslip at all — that employee is silently skipped.
    A missing PAN or bank account still produces a payslip, but the payment or
    the return will fail later, which is worse because it looks like it worked.
    """
    employees = (await db.execute(_active_employees(company_id))).scalars().all()
    structures = await _structures_by_employee(db, company_id)
    profiles = {
        p.employee_id: p
        for p in (
            await db.execute(select(EmployeeTaxProfile).where(EmployeeTaxProfile.company_id == company_id))
        )
        .scalars()
        .all()
    }

    records: list[dict[str, Any]] = []
    for e in employees:
        blocking: list[str] = []
        warning: list[str] = []

        structure = structures.get(e.id)
        if structure is None:
            blocking.append("Salary structure")
        if not (e.bank_account_no or "").strip():
            blocking.append("Bank account")

        if not (e.pan_card_number or "").strip():
            warning.append("PAN")
        if not (e.date_of_joining or e.hire_date):
            warning.append("Date of joining")
        if structure is not None:
            if structure.pf_enabled and not (e.uan or "").strip():
                warning.append("UAN (PF is on)")
            if structure.esi_enabled and not (e.esic_number or "").strip():
                warning.append("ESIC number (ESI is on)")
            if structure.pt_enabled and not (e.state or "").strip():
                # PT slabs are per state; without one the deduction silently computes zero.
                warning.append("State (professional tax is on)")
            if structure.tds_enabled and e.id not in profiles:
                warning.append("Tax declaration (TDS is on)")

        if not blocking and not warning:
            continue

        records.append(
            {
                "code": e.employee_id or "",
                "name": _name(e),
                "email": e.email or "",
                "employment_status": e.status or "",
                "blocking": "Yes" if blocking else "No",
                "missing": ", ".join(blocking + warning),
            }
        )

    # Blocking rows first — they are the ones that stop a run.
    records.sort(key=lambda r: (r["blocking"] != "Yes", str(r["name"]).lower()))
    return records


# ---------------------------------------------------------------------------
# 2. Skipped Summary — who a run left out
# ---------------------------------------------------------------------------
def skipped_summary_records(cycle: PayrollCycle) -> list[dict[str, Any]]:
    """Read the skip list the run persisted onto the cycle.

    Cycles run before this shipped have no ``skipped`` key. That is reported as
    an empty report rather than an error — the run genuinely may have skipped
    nobody, and the two cases are indistinguishable after the fact.
    """
    entries = (cycle.totals or {}).get("skipped") or []
    records = [
        {
            "name": str(entry.get("name") or ""),
            "employee_id": str(entry.get("employee_id") or ""),
            "reason": str(entry.get("reason") or ""),
        }
        for entry in entries
    ]
    records.sort(key=lambda r: str(r["name"]).lower())
    return records


# ---------------------------------------------------------------------------
# 3. Variance — what moved between two cycles
# ---------------------------------------------------------------------------
async def variance_records(
    db: AsyncSession, company_id: uuid.UUID, from_cycle: PayrollCycle, to_cycle: PayrollCycle
) -> list[dict[str, Any]]:
    """Per-employee movement between two runs, including joiners and leavers.

    An employee present in only one of the two cycles is still a row: appearing
    or disappearing is the largest variance there is, and dropping those rows is
    how a missed payment stays missed.
    """
    payslips = (
        (
            await db.execute(
                select(Payslip).where(
                    Payslip.company_id == company_id, Payslip.cycle_id.in_([from_cycle.id, to_cycle.id])
                )
            )
        )
        .scalars()
        .all()
    )
    employees = {e.id: e for e in (await db.execute(_active_employees(company_id))).scalars().all()}

    by_employee: dict[uuid.UUID, dict[str, Payslip]] = defaultdict(dict)
    for p in payslips:
        by_employee[p.employee_id]["from" if p.cycle_id == from_cycle.id else "to"] = p

    records: list[dict[str, Any]] = []
    for employee_id, pair in by_employee.items():
        e = employees.get(employee_id)
        before, after = pair.get("from"), pair.get("to")

        gross_from = float(before.gross_earnings) if before else 0.0
        gross_to = float(after.gross_earnings) if after else 0.0
        net_from = float(before.net_pay) if before else 0.0
        net_to = float(after.net_pay) if after else 0.0
        net_delta = net_to - net_from

        if before is None:
            movement = "New this cycle"
        elif after is None:
            movement = "Not paid this cycle"
        elif abs(net_delta) < 0.005:
            movement = "No change"
        else:
            movement = "Increase" if net_delta > 0 else "Decrease"

        records.append(
            {
                "code": (e.employee_id if e and e.employee_id else ""),
                "name": _name(e) if e else str(employee_id),
                "gross_from": gross_from,
                "gross_to": gross_to,
                "gross_delta": gross_to - gross_from,
                "net_from": net_from,
                "net_to": net_to,
                "net_delta": net_delta,
                # Guard the divide: a joiner has no previous net to compare against.
                "net_delta_pct": round(net_delta / net_from * 100, 2) if net_from else 0.0,
                "movement": movement,
            }
        )

    # Largest absolute movement first — that is what a reviewer is looking for.
    records.sort(key=lambda r: abs(float(r["net_delta"])), reverse=True)
    return records


# ---------------------------------------------------------------------------
# 4. Master CTC — the current package for everyone
# ---------------------------------------------------------------------------
async def master_ctc_records(db: AsyncSession, company_id: uuid.UUID) -> list[dict[str, Any]]:
    employees = {e.id: e for e in (await db.execute(_active_employees(company_id))).scalars().all()}
    structures = await _structures_by_employee(db, company_id)

    records: list[dict[str, Any]] = []
    for employee_id, s in structures.items():
        e = employees.get(employee_id)
        if e is None:
            continue
        ctc = Decimal(str(s.ctc or 0))
        records.append(
            {
                "code": e.employee_id or "",
                "name": _name(e),
                "designation": e.designation or "",
                "ctc": float(ctc),
                "currency": s.currency,
                "pay_frequency": s.pay_frequency,
                "monthly_gross": float(ctc / 12) if s.pay_frequency == "MONTHLY" else 0.0,
                "effective_from": s.effective_from.isoformat() if s.effective_from else "",
                "pf": "Yes" if s.pf_enabled else "No",
                "esi": "Yes" if s.esi_enabled else "No",
                "pt": "Yes" if s.pt_enabled else "No",
                "tds": "Yes" if s.tds_enabled else "No",
                "template": "Yes" if s.template_id else "Direct",
            }
        )
    records.sort(key=lambda r: str(r["name"]).lower())
    return records


# ---------------------------------------------------------------------------
# 5. HR Register — the employee master
# ---------------------------------------------------------------------------
async def hr_register_records(db: AsyncSession, company_id: uuid.UUID) -> list[dict[str, Any]]:
    employees = (await db.execute(_active_employees(company_id))).scalars().all()
    records = [
        {
            "code": e.employee_id or "",
            "name": _name(e),
            "email": e.email or "",
            "mobile": e.mobile or e.phone_number or "",
            "designation": e.designation or "",
            "employment_type": e.employment_type or "",
            "employment_status": e.status or "",
            "date_of_joining": (e.date_of_joining or e.hire_date).isoformat()
            if (e.date_of_joining or e.hire_date)
            else "",
            "probation_end": e.probation_end_date.isoformat() if e.probation_end_date else "",
            "notice_period": e.notice_period or 0,
            "pan": e.pan_card_number or "",
            "aadhaar": e.aadhar_card_number or "",
            "uan": e.uan or "",
            "esic": e.esic_number or "",
            "bank_account": e.bank_account_no or "",
            "state": e.state or "",
        }
        for e in employees
    ]
    records.sort(key=lambda r: str(r["name"]).lower())
    return records


# ---------------------------------------------------------------------------
# 6. Tax Computation — the annual position per employee
# ---------------------------------------------------------------------------
async def tax_computation_records(
    db: AsyncSession, company_id: uuid.UUID, financial_year: str = DEFAULT_FINANCIAL_YEAR
) -> list[dict[str, Any]]:
    """Year-to-date earnings and tax against what each employee declared.

    "Declaration" is the column worth reading. An employee with TDS on and no
    declaration is being taxed on the assumption of zero deductions, which is
    both wrong and the employee's complaint at year end.
    """
    employees = {e.id: e for e in (await db.execute(_active_employees(company_id))).scalars().all()}
    profiles = {
        p.employee_id: p
        for p in (
            await db.execute(
                select(EmployeeTaxProfile).where(
                    EmployeeTaxProfile.company_id == company_id,
                    EmployeeTaxProfile.financial_year == financial_year,
                )
            )
        )
        .scalars()
        .all()
    }
    structures = await _structures_by_employee(db, company_id)

    # Year-to-date is taken from payslips actually produced, not from the package,
    # so mid-year joiners and unpaid months are reflected.
    payslips = (await db.execute(select(Payslip).where(Payslip.company_id == company_id))).scalars().all()
    gross_ytd: dict[uuid.UUID, Decimal] = defaultdict(Decimal)
    tds_ytd: dict[uuid.UUID, Decimal] = defaultdict(Decimal)
    for p in payslips:
        gross_ytd[p.employee_id] += Decimal(str(p.gross_earnings or 0))
        tds_ytd[p.employee_id] += _statutory_amount(p, "TDS", "INCOME_TAX", "IT")

    records: list[dict[str, Any]] = []
    for employee_id, e in employees.items():
        profile = profiles.get(employee_id)
        structure = structures.get(employee_id)
        # Nothing to say about someone with no package and no declaration.
        if profile is None and structure is None:
            continue

        declared = {
            "declared_80c": float(profile.declared_80c) if profile else 0.0,
            "declared_80d": float(profile.declared_80d) if profile else 0.0,
            "declared_hra_rent": float(profile.declared_hra_rent) if profile else 0.0,
            "declared_home_loan": float(profile.declared_home_loan_interest) if profile else 0.0,
            "declared_other": float(profile.declared_other) if profile else 0.0,
        }
        prev_income = float(profile.prev_employer_income) if profile else 0.0
        gross = float(gross_ytd.get(employee_id, Decimal("0")))

        if profile is not None:
            declaration_state = "Declared"
        elif structure is not None and structure.tds_enabled:
            declaration_state = "MISSING — TDS is on"
        else:
            declaration_state = "Not required"

        records.append(
            {
                "code": e.employee_id or "",
                "name": _name(e),
                "pan": e.pan_card_number or "",
                "regime": profile.tax_regime if profile else "",
                "gross_ytd": gross,
                "prev_income": prev_income,
                "total_income": gross + prev_income,
                **declared,
                "total_declared": sum(declared.values()),
                "tds_ytd": float(tds_ytd.get(employee_id, Decimal("0"))),
                "prev_tds": float(profile.prev_employer_tds) if profile else 0.0,
                "declaration_state": declaration_state,
            }
        )

    # Missing declarations first — those are the ones to chase.
    records.sort(
        key=lambda r: (not str(r["declaration_state"]).startswith("MISSING"), str(r["name"]).lower())
    )
    return records


# ---------------------------------------------------------------------------
# 7. TDS — deducted against deposited, month by month
# ---------------------------------------------------------------------------
async def tds_records(
    db: AsyncSession, company_id: uuid.UUID, financial_year: str = DEFAULT_FINANCIAL_YEAR
) -> list[dict[str, Any]]:
    """Reconcile tax withheld from payslips against challans recorded as paid.

    This is the report that turns Croar's challan table from record-keeping into
    something actionable: a month where deducted exceeds deposited is an unpaid
    liability, and the difference is the amount owed.
    """
    challans = (
        (
            await db.execute(
                select(TdsChallan).where(
                    TdsChallan.company_id == company_id,
                    TdsChallan.financial_year == financial_year,
                    TdsChallan.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    # Deducted per month comes from the cycle a payslip belongs to, keyed on the
    # cycle's pay date — that is the month the liability arises.
    cycles = {
        c.id: c
        for c in (
            await db.execute(
                select(PayrollCycle).where(
                    PayrollCycle.company_id == company_id, PayrollCycle.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    }
    payslips = (await db.execute(select(Payslip).where(Payslip.company_id == company_id))).scalars().all()
    deducted: dict[str, Decimal] = defaultdict(Decimal)
    for p in payslips:
        cycle = cycles.get(p.cycle_id)
        if cycle is None:
            continue
        deducted[cycle.pay_date.strftime("%Y-%m")] += _statutory_amount(p, "TDS", "INCOME_TAX", "IT")

    deposited: dict[str, Decimal] = defaultdict(Decimal)
    challan_by_month: dict[str, TdsChallan] = {}
    for c in challans:
        deposited[c.period_month] += Decimal(str(c.amount or 0))
        challan_by_month.setdefault(c.period_month, c)

    records: list[dict[str, Any]] = []
    for month in sorted(set(deducted) | set(deposited)):
        due = deducted.get(month, Decimal("0"))
        paid = deposited.get(month, Decimal("0"))
        difference = due - paid
        challan = challan_by_month.get(month)

        if abs(difference) < Decimal("0.01"):
            state = "Reconciled"
        elif difference > 0:
            state = "Unpaid liability"
        else:
            state = "Overpaid"

        records.append(
            {
                "period_month": month,
                "deducted": float(due),
                "deposited": float(paid),
                "difference": float(difference),
                "challan_number": challan.challan_number if challan else "",
                "bsr_code": (challan.bsr_code or "") if challan else "",
                "deposit_date": challan.deposit_date.isoformat() if challan else "",
                "interest": float(challan.interest) if challan else 0.0,
                "penalty": float(challan.penalty) if challan else 0.0,
                "reconciliation": state,
            }
        )
    return records


# ---------------------------------------------------------------------------
# PDF wrappers — the table renderer is shared; only weights differ
# ---------------------------------------------------------------------------
def _pdf(title: str, subtitle: str, columns: list[Column], records: list[dict[str, Any]]) -> bytes:
    return _table_pdf(title, subtitle, [(h, k, n, 1.0) for h, k, n in columns], records)


def missing_information_pdf(company_name: str, records: list[dict[str, Any]]) -> bytes:
    blocking = sum(1 for r in records if r.get("blocking") == "Yes")
    subtitle = f"{company_name}  ·  {len(records)} employee(s) with gaps  ·  {blocking} blocking payment"
    return _pdf("Missing Information", subtitle, MISSING_INFO_COLUMNS, records)


def skipped_summary_pdf(company_name: str, cycle: PayrollCycle, records: list[dict[str, Any]]) -> bytes:
    subtitle = f"{company_name}  ·  {cycle.name}  ·  {len(records)} employee(s) not paid"
    return _pdf("Skipped Summary", subtitle, SKIPPED_SUMMARY_COLUMNS, records)


def variance_pdf(
    company_name: str, from_cycle: PayrollCycle, to_cycle: PayrollCycle, records: list[dict[str, Any]]
) -> bytes:
    subtitle = f"{company_name}  ·  {from_cycle.name}  to  {to_cycle.name}  ·  {len(records)} employee(s)"
    return _pdf("Variance Report", subtitle, VARIANCE_COLUMNS, records)


def master_ctc_pdf(company_name: str, records: list[dict[str, Any]]) -> bytes:
    return _pdf("Master CTC", f"{company_name}  ·  {len(records)} employee(s)", MASTER_CTC_COLUMNS, records)


def hr_register_pdf(company_name: str, records: list[dict[str, Any]]) -> bytes:
    return _pdf("HR Register", f"{company_name}  ·  {len(records)} employee(s)", HR_REGISTER_COLUMNS, records)


def tax_computation_pdf(company_name: str, financial_year: str, records: list[dict[str, Any]]) -> bytes:
    missing = sum(1 for r in records if str(r.get("declaration_state", "")).startswith("MISSING"))
    subtitle = f"{company_name}  ·  FY {financial_year}  ·  {len(records)} employee(s)  ·  {missing} without a declaration"
    return _pdf("Tax Computation", subtitle, TAX_COMPUTATION_COLUMNS, records)


def tds_pdf(company_name: str, financial_year: str, records: list[dict[str, Any]]) -> bytes:
    unpaid = sum(1 for r in records if r.get("reconciliation") == "Unpaid liability")
    subtitle = f"{company_name}  ·  FY {financial_year}  ·  {len(records)} month(s)  ·  {unpaid} unreconciled"
    return _pdf("TDS Reconciliation", subtitle, TDS_COLUMNS, records)
