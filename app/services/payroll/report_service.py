"""Payroll reports & registers (read-side exports).

Builds tabular reports from existing payslip/cycle data and serialises them to:
- CSV  (stdlib ``csv``; UTF-8 BOM so Excel opens it correctly)
- PDF  (fpdf2 — already the payslip dependency; no native/extra deps)

XLSX is intentionally omitted to keep the project dependency-light (the rest of
the codebase avoids native deps); CSV opens directly in Excel. Add openpyxl
later if a true .xlsx is required.
"""

import csv
import io
import uuid
from typing import Any

from fpdf import FPDF
from fpdf.enums import XPos, YPos
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enterprise.employee import Employee
from app.models.payroll import PayrollCycle, Payslip

_MUTED = (107, 114, 128)
_HEAD_FILL = (243, 244, 246)


# ---------------------------------------------------------------------------
# Column definitions: (header, record-key, is_numeric)
# ---------------------------------------------------------------------------
Column = tuple[str, str, bool]

SALARY_REGISTER_COLUMNS: list[Column] = [
    ("Employee Code", "code", False),
    ("Employee Name", "name", False),
    ("Email", "email", False),
    ("PAN", "pan", False),
    ("LOP Days", "lop_days", True),
    ("Paid Days", "paid_days", True),
    ("Gross", "gross", True),
    ("Deductions", "deductions", True),
    ("Net Pay", "net", True),
    ("Currency", "currency", False),
    ("Status", "status", False),
]

# A trimmed, readable column set for the (landscape) PDF; weights are relative.
SALARY_REGISTER_PDF_COLUMNS: list[tuple[str, str, bool, float]] = [
    ("Code", "code", False, 1.1),
    ("Name", "name", False, 2.4),
    ("PAN", "pan", False, 1.4),
    ("LOP", "lop_days", True, 0.8),
    ("Paid", "paid_days", True, 0.8),
    ("Gross", "gross", True, 1.6),
    ("Deductions", "deductions", True, 1.6),
    ("Net Pay", "net", True, 1.6),
    ("Status", "status", False, 1.2),
]

PAYROLL_SUMMARY_COLUMNS: list[Column] = [
    ("Cycle", "name", False),
    ("Period Start", "period_start", False),
    ("Period End", "period_end", False),
    ("Pay Date", "pay_date", False),
    ("Status", "status", False),
    ("Headcount", "headcount", True),
    ("Gross", "gross", True),
    ("Deductions", "deductions", True),
    ("Net", "net", True),
    ("Employer Cost", "employer_cost", True),
    ("Total Cost", "total_cost", True),
]


# ---------------------------------------------------------------------------
# Record builders
# ---------------------------------------------------------------------------
async def salary_register_records(
    db: AsyncSession, company_id: uuid.UUID, cycle: PayrollCycle
) -> list[dict[str, Any]]:
    """One record per payslip in the cycle, joined to employee details."""
    payslips = (
        (
            await db.execute(
                select(Payslip).where(Payslip.cycle_id == cycle.id, Payslip.company_id == company_id)
            )
        )
        .scalars()
        .all()
    )
    employees = {
        e.id: e
        for e in (await db.execute(select(Employee).where(Employee.company_id == company_id))).scalars().all()
    }
    records: list[dict[str, Any]] = []
    for p in payslips:
        e = employees.get(p.employee_id)
        name = f"{e.first_name} {e.last_name}".strip() if e else str(p.employee_id)
        records.append(
            {
                "code": (e.employee_id if e and e.employee_id else ""),
                "name": name,
                "email": (e.email if e else ""),
                "pan": (e.pan if e and e.pan else ""),
                "lop_days": float(p.lop_days or 0),
                "paid_days": float(p.paid_days or 0),
                "gross": float(p.gross_earnings),
                "deductions": float(p.total_deductions),
                "net": float(p.net_pay),
                "currency": p.currency,
                "status": p.status,
            }
        )
    records.sort(key=lambda r: str(r["name"]).lower())
    return records


async def payroll_summary_records(db: AsyncSession, company_id: uuid.UUID) -> list[dict[str, Any]]:
    """One record per (non-deleted) cycle, from its rolled-up totals."""
    cycles = (
        (
            await db.execute(
                select(PayrollCycle)
                .where(PayrollCycle.company_id == company_id, PayrollCycle.deleted_at.is_(None))
                .order_by(PayrollCycle.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    records: list[dict[str, Any]] = []
    for c in cycles:
        t = c.totals or {}
        records.append(
            {
                "name": c.name,
                "period_start": c.period_start.isoformat(),
                "period_end": c.period_end.isoformat(),
                "pay_date": c.pay_date.isoformat(),
                "status": c.status,
                "headcount": int(t.get("headcount", 0) or 0),
                "gross": float(t.get("gross", 0) or 0),
                "deductions": float(t.get("deductions", 0) or 0),
                "net": float(t.get("net", 0) or 0),
                "employer_cost": float(t.get("employer_cost", 0) or 0),
                "total_cost": float(t.get("total_cost", 0) or 0),
            }
        )
    return records


# ---------------------------------------------------------------------------
# Serialisers
# ---------------------------------------------------------------------------
def _fmt(value: Any, numeric: bool) -> str:
    if numeric:
        return f"{float(value or 0):,.2f}" if isinstance(value, float) else str(value)
    return "" if value is None else str(value)


def records_to_csv(columns: list[Column], records: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([header for header, _, _ in columns])
    for rec in records:
        writer.writerow([rec.get(key, "") for _, key, _ in columns])
    # utf-8-sig: prepend a BOM so Excel detects UTF-8 (₹, accents) correctly.
    return buf.getvalue().encode("utf-8-sig")


def _table_pdf(
    title: str,
    subtitle: str,
    columns: list[tuple[str, str, bool, float]],
    records: list[dict[str, Any]],
    *,
    landscape: bool = True,
) -> bytes:
    pdf = FPDF(orientation="L" if landscape else "P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    if subtitle:
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*_MUTED)
        pdf.cell(0, 5, subtitle, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(0, 0, 0)
    pdf.ln(2)

    usable = pdf.w - pdf.l_margin - pdf.r_margin
    weight_total = sum(w for *_, w in columns)
    widths = [usable * w / weight_total for *_, w in columns]

    def _row(values: list[str], *, head: bool) -> None:
        pdf.set_font("Helvetica", "B" if head else "", 8)
        if head:
            pdf.set_fill_color(*_HEAD_FILL)
        for (header, key, numeric, _), width in zip(columns, widths):
            text = header if head else _fmt(values_map.get(key), numeric)
            # Truncate to fit the cell width (rough char budget at 8pt).
            budget = max(3, int(width / 1.7))
            if len(text) > budget:
                text = text[: budget - 1] + "…"
            align = "R" if (numeric and not head) else ("C" if head else "L")
            pdf.cell(width, 6.5, text, border=1, align=align, fill=head)
        pdf.ln()

    values_map: dict[str, Any] = {}
    _row([], head=True)
    if not records:
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(*_MUTED)
        pdf.cell(0, 8, "No data for this report.", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    for rec in records:
        values_map = rec
        _row([], head=False)
    return bytes(pdf.output())


def salary_register_pdf(company_name: str, cycle: PayrollCycle, records: list[dict[str, Any]]) -> bytes:
    subtitle = (
        f"{company_name}  ·  {cycle.name}  ·  {cycle.period_start} to {cycle.period_end}"
        f"  ·  Pay date {cycle.pay_date}  ·  {len(records)} employee(s)"
    )
    return _table_pdf("Salary Register", subtitle, SALARY_REGISTER_PDF_COLUMNS, records)


def payroll_summary_pdf(company_name: str, records: list[dict[str, Any]]) -> bytes:
    pdf_columns = [(h, k, n, 1.0) for h, k, n in PAYROLL_SUMMARY_COLUMNS]
    return _table_pdf("Payroll Summary", f"{company_name}  ·  {len(records)} cycle(s)", pdf_columns, records)
