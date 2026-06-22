import uuid
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enterprise.company import Company
from app.models.enterprise.employee import Employee
from app.models.payroll import PayrollAdjustment, PayrollCycle, Payslip, SalaryStructure, SalaryTemplate
from app.models.payroll.taxes import EmployeeTaxProfile
from app.payroll.constants import (
    CTC_CODE,
    DEFAULT_WORKING_DAYS,
    AdjustmentKind,
    LineType,
    PayFrequency,
    PayrollCycleStatus,
    PayslipStatus,
    TaxRegime,
)
from app.schemas.enterprise.payroll.settings import StatutoryConfig
from app.services.payroll import calendar_service, tax_engine, timesheet_service
from app.services.payroll import statutory as statutory_rules


def _dec(value: float | int) -> Decimal:
    """Config values arrive as floats (JSON-native); convert for the engine."""
    return Decimal(str(value))


def _q(amount: Decimal) -> Decimal:
    """Round a money amount to 2 dp (ROUND_HALF_UP)."""
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _resolve_amount(line: dict[str, Any], by_code: dict[str, Decimal], base_when_omitted: Decimal) -> Decimal:
    """Resolve a single money line to an absolute amount.

    - fixed:   the line's `amount` (monthly value).
    - percent: `percent`% of the `percent_of` line's resolved amount, or of
               `base_when_omitted` (gross-so-far for earnings, gross for
               deductions) when `percent_of` is omitted.
    """
    line_type = line.get("type")
    if line_type == LineType.FIXED.value:
        return Decimal(str(line.get("amount") or "0"))
    if line_type == LineType.PERCENT.value:
        pct = Decimal(str(line.get("percent") or "0"))
        percent_of = line.get("percent_of")
        base = by_code.get(percent_of, Decimal("0")) if percent_of else base_when_omitted
        return (pct / Decimal("100")) * base
    return Decimal("0")


def compute_payslip(
    structure: SalaryStructure,
    lop_days: Decimal,
    working_days: Decimal,
    *,
    pt_state: str | None = None,
    adjustments: list[dict[str, Any]] | None = None,
    tds_enabled: bool = False,
    tax_profile: dict[str, Any] | None = None,
    statutory: StatutoryConfig | None = None,
) -> dict[str, Any]:
    """Pure payslip calculation (spec §5).

    1. Resolve each earning line (fixed / percent-of-code / percent-of-gross).
    2. gross = sum(earnings); pro-rate by paid/working days when lop_days > 0.
    3. Resolve deductions the same way (percent_of a code, or percent of gross).
    4. Apply statutory deductions (PF/ESI/PT) when enabled on the structure.
    5. Apply per-run adjustments (one-time earnings/deductions for this cycle).
    6. net = gross - (voluntary + statutory + adjustment) deductions.

    Statutory is opt-in per structure; with all toggles off the result is
    identical to the pre-statutory calculation. Each resolved line is rounded to
    2 dp before summing (avoids cent drift).

    Adjustments are flat absolute amounts (``{kind, code, label, amount}``): they
    are added on top of the structure result, are NOT LOP-prorated (a bonus or
    arrear is an absolute figure), and do NOT change the statutory wage base —
    PF/ESI/PT are computed on regular structure earnings in step 4.
    """
    if working_days <= 0:
        raise ValueError("working_days must be greater than zero")
    if lop_days < 0:
        raise ValueError("lop_days cannot be negative")
    if lop_days > working_days:
        raise ValueError("lop_days cannot exceed working_days")

    paid_days = working_days - lop_days
    multiplier = (paid_days / working_days) if lop_days > 0 else Decimal("1")

    # Per-period cost-to-company: lets components be defined as a % of CTC and a
    # "balance" line absorb the remainder, so the package stays CTC-driven.
    # Components are monthly amounts on a 30-day basis, so MONTHLY divides the
    # annual CTC by 12 (WEEKLY by 52).
    divisor = Decimal("52") if str(structure.pay_frequency) == PayFrequency.WEEKLY.value else Decimal("12")
    period_ctc = _q(Decimal(str(structure.ctc)) / divisor) if structure.ctc else Decimal("0")

    # --- Pass 1: resolve earnings on the raw (un-prorated) basis ---
    # "CTC" is a reserved reference (not an earning, so it never adds to gross);
    # percent lines may target it via percent_of. Balance lines are deferred to a
    # second pass since they depend on the sum of every other earning.
    raw_by_code: dict[str, Decimal] = {CTC_CODE: period_ctc}
    raw_gross = Decimal("0")
    raw_lines: list[tuple[str, str, Decimal]] = []
    balance_lines: list[dict[str, Any]] = []
    for line in structure.components or []:
        if line.get("type") == LineType.BALANCE.value:
            balance_lines.append(line)
            continue
        amt = _q(_resolve_amount(line, raw_by_code, raw_gross))
        code = line["code"]
        raw_by_code[code] = amt
        raw_gross += amt
        raw_lines.append((code, line.get("label", code), amt))

    # Balance line(s) split the CTC remainder (never negative); shared equally if
    # more than one is defined.
    if balance_lines:
        remainder = period_ctc - raw_gross
        share = _q(remainder / Decimal(len(balance_lines))) if remainder > 0 else Decimal("0")
        for line in balance_lines:
            code = line["code"]
            raw_by_code[code] = share
            raw_gross += share
            raw_lines.append((code, line.get("label", code), share))

    # --- Apply LOP pro-ration uniformly to each earning line ---
    earnings: list[dict[str, Any]] = []
    by_code: dict[str, Decimal] = {}
    gross = Decimal("0.00")
    for code, label, raw in raw_lines:
        amt = _q(raw * multiplier)
        by_code[code] = amt
        gross = _q(gross + amt)
        earnings.append({"code": code, "label": label, "amount": float(amt)})

    # --- Deductions (resolved against prorated earnings + gross) ---
    # CTC is also referenceable here (a fixed per-period base, not prorated).
    deductions: list[dict[str, Any]] = []
    ded_ref: dict[str, Decimal] = {CTC_CODE: period_ctc, **by_code}
    total_deductions = Decimal("0.00")
    for line in structure.default_deductions or []:
        amt = _q(_resolve_amount(line, ded_ref, gross))
        code = line["code"]
        ded_ref[code] = amt
        total_deductions = _q(total_deductions + amt)
        deductions.append({"code": code, "label": line.get("label", code), "amount": float(amt)})

    # --- Statutory (PF / ESI / PT) — opt-in per structure ---
    # Resolve the company's statutory config (rates/thresholds); defaults mirror
    # the code constants when no override is supplied.
    cfg = statutory or StatutoryConfig()
    employer_contributions: list[dict[str, Any]] = []
    statutory_snapshot: dict[str, Any] = {"version": statutory_rules.RULESET_VERSION}

    if structure.pf_enabled:
        codes = structure.pf_wage_codes or ["BASIC"]
        pf_wage = sum((by_code.get(c, Decimal("0")) for c in codes), Decimal("0.00"))
        if pf_wage <= 0:  # fall back to gross if the named codes aren't present
            pf_wage = gross
        cap = True if structure.pf_cap_at_ceiling is None else bool(structure.pf_cap_at_ceiling)
        pf = statutory_rules.compute_pf(
            pf_wage,
            cap_at_ceiling=cap,
            employee_rate=_dec(cfg.pf_employee_rate),
            employer_rate=_dec(cfg.pf_employer_rate),
            wage_ceiling=_dec(cfg.pf_wage_ceiling),
            eps_rate=_dec(cfg.eps_rate),
            eps_wage_ceiling=_dec(cfg.eps_wage_ceiling),
        )
        deductions.append(
            {"code": "PF", "label": "Provident Fund (Employee)", "amount": float(pf["employee"])}
        )
        total_deductions = _q(total_deductions + pf["employee"])
        employer_contributions.append(
            {"code": "PF_ER", "label": "Provident Fund (Employer)", "amount": float(pf["employer_epf"])}
        )
        employer_contributions.append(
            {"code": "EPS_ER", "label": "Pension (EPS, Employer)", "amount": float(pf["employer_eps"])}
        )
        statutory_snapshot["pf"] = {k: float(v) for k, v in pf.items()}

    if structure.esi_enabled:
        esi = statutory_rules.compute_esi(
            gross,
            wage_limit=_dec(cfg.esi_wage_limit),
            employee_rate=_dec(cfg.esi_employee_rate),
            employer_rate=_dec(cfg.esi_employer_rate),
        )
        statutory_snapshot["esi"] = {
            "covered": esi["covered"],
            "employee": float(esi["employee"]),
            "employer": float(esi["employer"]),
        }
        if esi["covered"]:
            deductions.append({"code": "ESI", "label": "ESI (Employee)", "amount": float(esi["employee"])})
            total_deductions = _q(total_deductions + esi["employee"])
            employer_contributions.append(
                {"code": "ESI_ER", "label": "ESI (Employer)", "amount": float(esi["employer"])}
            )

    if structure.pt_enabled:
        pt = statutory_rules.compute_pt(pt_state, gross)
        statutory_snapshot["pt"] = {"amount": float(pt["amount"]), "state": pt["state"], "note": pt["note"]}
        if pt["amount"] > 0:
            deductions.append({"code": "PT", "label": "Professional Tax", "amount": float(pt["amount"])})
            total_deductions = _q(total_deductions + pt["amount"])

    # --- Income tax (TDS) — opt-in per structure (Phase 2, ESTIMATE) ---
    # Annual projection uses the stable monthly gross (raw_gross, pre-LOP) x 12
    # so monthly TDS doesn't swing with LOP. Driven by the employee's IT
    # declaration; result is snapshotted and added as a deduction line.
    if tds_enabled:
        profile = tax_profile or {}
        tds = tax_engine.compute_tds(
            annual_gross=_q(raw_gross * Decimal("12")),
            regime=str(profile.get("tax_regime") or TaxRegime.NEW.value),
            declarations=profile,
            prev_employer_income=Decimal(str(profile.get("prev_employer_income") or "0")),
            prev_employer_tds=Decimal(str(profile.get("prev_employer_tds") or "0")),
            new_rebate_limit=_dec(cfg.tds_new_rebate_limit),
            old_rebate_limit=_dec(cfg.tds_old_rebate_limit),
            new_std_deduction=_dec(cfg.tds_new_std_deduction),
            old_std_deduction=_dec(cfg.tds_old_std_deduction),
        )
        statutory_snapshot["tds"] = {k: (float(v) if isinstance(v, Decimal) else v) for k, v in tds.items()}
        monthly_tds = tds["monthly_tds"]
        if monthly_tds > 0:
            deductions.append(
                {"code": "TDS", "label": "TDS (Income Tax, est.)", "amount": float(monthly_tds)}
            )
            total_deductions = _q(total_deductions + monthly_tds)

    # --- Per-run adjustments (one-time earnings/deductions for THIS cycle) ---
    # Flat amounts layered on top of the structure result; see the docstring.
    for adj in adjustments or []:
        amt = _q(Decimal(str(adj.get("amount") or "0")))
        code = adj.get("code", "")
        label = adj.get("label", code)
        if adj.get("kind") == AdjustmentKind.EARNING.value:
            earnings.append({"code": code, "label": label, "amount": float(amt)})
            gross = _q(gross + amt)
        elif adj.get("kind") == AdjustmentKind.DEDUCTION.value:
            deductions.append({"code": code, "label": label, "amount": float(amt)})
            total_deductions = _q(total_deductions + amt)

    employer_total = sum((Decimal(str(c["amount"])) for c in employer_contributions), Decimal("0.00"))
    net_pay = _q(gross - total_deductions)

    return {
        "earnings": earnings,
        "deductions": deductions,
        "gross_earnings": gross,
        "total_deductions": total_deductions,
        "net_pay": net_pay,
        "lop_days": lop_days,
        "paid_days": paid_days,
        "employer_contributions": employer_contributions,
        "employer_total": _q(employer_total),
        "statutory": statutory_snapshot,
    }


def compute_hourly_payslip(
    structure: SalaryStructure,
    total_hours: Decimal,
    *,
    pt_state: str | None = None,
    adjustments: list[dict[str, Any]] | None = None,
    tds_enabled: bool = False,
    tax_profile: dict[str, Any] | None = None,
    statutory: StatutoryConfig | None = None,
) -> dict[str, Any]:
    """Payslip for an hourly-paid employee: gross = total_hours * hourly_rate.

    Reuses ``compute_payslip`` by synthesising a single fixed "WAGES" earning
    from the hours worked, with no LOP proration (the hours already reflect
    actual work). Statutory (PF/ESI/PT/TDS) and the structure's own deduction
    lines still apply on the wage gross.
    """
    rate = Decimal(str(structure.hourly_rate or "0"))
    wage = _q(total_hours * rate)
    transient = SalaryStructure(
        company_id=structure.company_id,
        ctc=Decimal("0"),
        currency=structure.currency,
        pay_frequency=PayFrequency.MONTHLY.value,
        components=[{"code": "WAGES", "label": "Wages", "type": LineType.FIXED.value, "amount": float(wage)}],
        default_deductions=structure.default_deductions or [],
        pf_enabled=structure.pf_enabled,
        pf_cap_at_ceiling=structure.pf_cap_at_ceiling,
        pf_wage_codes=structure.pf_wage_codes,
        esi_enabled=structure.esi_enabled,
        pt_enabled=structure.pt_enabled,
        tds_enabled=structure.tds_enabled,
    )
    return compute_payslip(
        transient,
        Decimal("0"),
        Decimal("1"),
        pt_state=pt_state,
        adjustments=adjustments,
        tds_enabled=tds_enabled,
        tax_profile=tax_profile,
        statutory=statutory,
    )


async def _load_statutory_config(db: AsyncSession, company_id: uuid.UUID) -> StatutoryConfig:
    """The company's statutory rate/threshold overrides (defaults if unset)."""
    raw = (
        await db.execute(select(Company.statutory_settings).where(Company.id == company_id))
    ).scalar_one_or_none()
    return StatutoryConfig.from_stored(raw)


async def preview_structure(
    db: AsyncSession, company_id: uuid.UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    """Compute a payslip for an UNSAVED structure draft (no persistence).

    Drives the live "Estimated Monthly Salary" preview on the structure form so
    it reflects the SAME engine as a real run — including statutory (PF/ESI/PT)
    and the TDS estimate. When ``employee_id`` is supplied we load that
    employee's state (for PT) and IT declaration (for TDS) so the preview
    matches what the next payroll run will produce.
    """
    working_days = Decimal(DEFAULT_WORKING_DAYS)
    # Clamp LOP into range so a half-typed value never 500s the live preview.
    raw_lop = Decimal(str(payload.get("lop_days") or "0"))
    lop_days = max(Decimal("0"), min(raw_lop, working_days))

    pt_state: str | None = None
    tax_profile: dict[str, Any] | None = None
    employee_id = payload.get("employee_id")
    if employee_id is not None:
        employee = (
            await db.execute(
                select(Employee).where(
                    Employee.id == employee_id,
                    Employee.company_id == company_id,
                    Employee.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if employee is not None:
            pt_state = employee.state
            if payload.get("tds_enabled"):
                profile = (
                    await db.execute(
                        select(EmployeeTaxProfile).where(
                            EmployeeTaxProfile.employee_id == employee_id,
                            EmployeeTaxProfile.company_id == company_id,
                            EmployeeTaxProfile.deleted_at.is_(None),
                        )
                    )
                ).scalar_one_or_none()
                if profile is not None:
                    tax_profile = {
                        "tax_regime": profile.tax_regime,
                        "declared_80c": profile.declared_80c,
                        "declared_80d": profile.declared_80d,
                        "declared_hra_rent": profile.declared_hra_rent,
                        "declared_home_loan_interest": profile.declared_home_loan_interest,
                        "declared_other": profile.declared_other,
                        "prev_employer_income": profile.prev_employer_income,
                        "prev_employer_tds": profile.prev_employer_tds,
                    }

    # Transient (unsaved) structure — compute_payslip only reads attributes.
    struct = SalaryStructure(
        company_id=company_id,
        ctc=Decimal(str(payload.get("ctc") or "0")),
        pay_frequency=str(payload.get("pay_frequency") or PayFrequency.MONTHLY.value),
        components=payload.get("components") or [],
        default_deductions=payload.get("default_deductions") or [],
        pf_enabled=bool(payload.get("pf_enabled")),
        pf_cap_at_ceiling=bool(payload.get("pf_cap_at_ceiling", True)),
        pf_wage_codes=payload.get("pf_wage_codes"),
        esi_enabled=bool(payload.get("esi_enabled")),
        pt_enabled=bool(payload.get("pt_enabled")),
        tds_enabled=bool(payload.get("tds_enabled")),
    )
    return compute_payslip(
        struct,
        lop_days,
        working_days,
        pt_state=pt_state,
        tds_enabled=bool(payload.get("tds_enabled")),
        tax_profile=tax_profile,
        statutory=await _load_statutory_config(db, company_id),
    )


async def apply_template(
    db: AsyncSession,
    company_id: uuid.UUID,
    template_id: uuid.UUID,
    assignments: list[dict[str, Any]],
    replace_existing: bool,
) -> dict[str, Any]:
    """Generate per-employee salary structures from a template.

    The template's component rules (percent-of-CTC + balance line) are snapshotted
    onto each structure, stamped with that employee's CTC and effective date — so
    the same template yields different absolute amounts per employee while staying
    CTC-driven. Employees that don't exist (or already have an active structure
    when ``replace_existing`` is False) are skipped, not failed.
    """
    template = (
        await db.execute(
            select(SalaryTemplate).where(
                SalaryTemplate.id == template_id,
                SalaryTemplate.company_id == company_id,
                SalaryTemplate.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")

    created: list[uuid.UUID] = []
    skipped: list[dict[str, Any]] = []
    try:
        for a in assignments:
            employee_id = a["employee_id"]
            employee = (
                await db.execute(
                    select(Employee).where(
                        Employee.id == employee_id,
                        Employee.company_id == company_id,
                        Employee.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if employee is None:
                skipped.append({"employee_id": employee_id, "reason": "Employee not found"})
                continue

            existing = (
                await db.execute(
                    select(SalaryStructure).where(
                        SalaryStructure.employee_id == employee_id,
                        SalaryStructure.company_id == company_id,
                        SalaryStructure.is_active.is_(True),
                        SalaryStructure.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                if not replace_existing:
                    skipped.append({"employee_id": employee_id, "reason": "Already has an active structure"})
                    continue
                # Deactivate the old structure first (flush) so the single-active
                # partial unique index never sees two active rows at once.
                existing.is_active = False
                await db.flush()

            struct = SalaryStructure(
                company_id=company_id,
                employee_id=employee_id,
                template_id=template.id,
                ctc=Decimal(str(a["ctc"])),
                currency=template.currency,
                pay_frequency=template.pay_frequency,
                effective_from=a["effective_from"],
                components=template.components or [],
                default_deductions=template.default_deductions or [],
                lop_days=Decimal("0"),
                is_active=True,
                pf_enabled=template.pf_enabled,
                pf_cap_at_ceiling=template.pf_cap_at_ceiling,
                pf_wage_codes=template.pf_wage_codes,
                esi_enabled=template.esi_enabled,
                pt_enabled=template.pt_enabled,
                tds_enabled=template.tds_enabled,
            )
            db.add(struct)
            await db.flush()
            created.append(struct.id)
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return {"created": created, "skipped": skipped}


async def _load_cycle(db: AsyncSession, cycle_id: uuid.UUID, company_id: uuid.UUID) -> PayrollCycle:
    stmt = select(PayrollCycle).where(
        PayrollCycle.id == cycle_id, PayrollCycle.company_id == company_id, PayrollCycle.deleted_at.is_(None)
    )
    cycle = (await db.execute(stmt)).scalar_one_or_none()
    if not cycle:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payroll cycle not found")
    return cycle


def _cycle_brief(cycle: PayrollCycle) -> dict[str, Any]:
    totals = cycle.totals or {}
    return {
        "id": cycle.id,
        "name": cycle.name,
        "status": cycle.status,
        "period_start": cycle.period_start,
        "period_end": cycle.period_end,
        "pay_date": cycle.pay_date,
        "net": Decimal(str(totals.get("net", 0))),
        "headcount": int(totals.get("headcount", 0) or 0),
    }


async def dashboard_summary(db: AsyncSession, company_id: uuid.UUID) -> dict[str, Any]:
    """Application-wide overview for the dashboard (all server-side, one call).

    Aggregates employees, salary configuration coverage, payroll-cycle status
    counts, money disbursed/pending, and the most recent cycles. Everything is
    scoped to the caller's company.
    """
    # --- Employees (active) ---
    employees = (
        (
            await db.execute(
                select(Employee.id).where(Employee.company_id == company_id, Employee.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    employee_ids = set(employees)

    # --- Active salary structures + which employees are configured ---
    structure_emp_ids = (
        (
            await db.execute(
                select(SalaryStructure.employee_id).where(
                    SalaryStructure.company_id == company_id,
                    SalaryStructure.is_active.is_(True),
                    SalaryStructure.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    configured_ids = set(structure_emp_ids) & employee_ids
    active_structures = len(structure_emp_ids)

    # --- Cycles (non-deleted) ---
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

    by_status: dict[str, int] = {s.value: 0 for s in PayrollCycleStatus}
    gross_paid = net_paid = pending_net = Decimal("0.00")
    payslips_paid = 0
    for cycle in cycles:
        by_status[cycle.status] = by_status.get(cycle.status, 0) + 1
        totals = cycle.totals or {}
        net = Decimal(str(totals.get("net", 0)))
        gross = Decimal(str(totals.get("gross", 0)))
        headcount = int(totals.get("headcount", 0) or 0)
        if cycle.status == PayrollCycleStatus.PAID.value:
            net_paid += net
            gross_paid += gross
            payslips_paid += headcount
        elif cycle.status in (PayrollCycleStatus.PROCESSING.value, PayrollCycleStatus.APPROVED.value):
            pending_net += net

    # Current = most recent cycle still in flight; else most recent overall.
    current = next(
        (
            c
            for c in cycles
            if c.status not in (PayrollCycleStatus.PAID.value, PayrollCycleStatus.CANCELLED.value)
        ),
        cycles[0] if cycles else None,
    )

    return {
        "employees": {
            "total": len(employee_ids),
            "configured": len(configured_ids),
            "missing": len(employee_ids - configured_ids),
        },
        "active_structures": active_structures,
        "cycles": {"total": len(cycles), "by_status": by_status},
        "payroll": {
            "gross_paid": gross_paid,
            "net_paid": net_paid,
            "payslips_paid": payslips_paid,
            "pending_net": pending_net,
        },
        "current_cycle": _cycle_brief(current) if current else None,
        "recent_cycles": [_cycle_brief(c) for c in cycles[:5]],
        "currency": "INR",
    }


async def run_payroll(db: AsyncSession, cycle_id: uuid.UUID, company_id: uuid.UUID) -> dict[str, Any]:
    """Generate / refresh payslips for every active employee with a structure.

    Idempotent: upserts on (cycle_id, employee_id). Employees without an active
    salary structure are returned in `skipped` (never silently dropped).
    """
    cycle = await _load_cycle(db, cycle_id, company_id)

    if cycle.status not in (PayrollCycleStatus.DRAFT, PayrollCycleStatus.PROCESSING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cycle must be DRAFT or PROCESSING to run (is {cycle.status})",
        )

    # Proration denominator: derived from the company work calendar (weekly-offs
    # + holidays) over the cycle period, or the fixed DEFAULT_WORKING_DAYS when
    # the company has calendar-derived working days disabled.
    working_days = await calendar_service.working_days_in_period(
        db, company_id, cycle.period_start, cycle.period_end
    )

    employees = (
        (
            await db.execute(
                select(Employee).where(Employee.company_id == company_id, Employee.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    )

    # Per-run adjustments for this cycle, grouped by employee. Applied on top of
    # each payslip in compute_payslip (one-time bonuses/arrears/deductions).
    adjustments = (
        (
            await db.execute(
                select(PayrollAdjustment).where(
                    PayrollAdjustment.cycle_id == cycle_id,
                    PayrollAdjustment.company_id == company_id,
                    PayrollAdjustment.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    adjustments_by_employee: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for adj in adjustments:
        adjustments_by_employee.setdefault(adj.employee_id, []).append(
            {"kind": adj.kind, "code": adj.code, "label": adj.label, "amount": adj.amount}
        )

    # Employee IT declarations (for TDS estimation), keyed by employee.
    tax_profiles = (
        (
            await db.execute(
                select(EmployeeTaxProfile).where(
                    EmployeeTaxProfile.company_id == company_id, EmployeeTaxProfile.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    tax_profile_by_employee: dict[uuid.UUID, dict[str, Any]] = {
        p.employee_id: {
            "tax_regime": p.tax_regime,
            "declared_80c": p.declared_80c,
            "declared_80d": p.declared_80d,
            "declared_hra_rent": p.declared_hra_rent,
            "declared_home_loan_interest": p.declared_home_loan_interest,
            "declared_other": p.declared_other,
            "prev_employer_income": p.prev_employer_income,
            "prev_employer_tds": p.prev_employer_tds,
        }
        for p in tax_profiles
    }

    # Company statutory overrides (rates/thresholds) — loaded once for the run.
    statutory_config = await _load_statutory_config(db, company_id)

    created_count = 0
    updated_count = 0
    skipped: list[dict[str, Any]] = []

    for employee in employees:
        struct = (
            await db.execute(
                select(SalaryStructure).where(
                    SalaryStructure.employee_id == employee.id,
                    SalaryStructure.company_id == company_id,
                    SalaryStructure.is_active.is_(True),
                    SalaryStructure.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

        if not struct:
            skipped.append({"employee_id": employee.id, "reason": "no active salary structure"})
            continue

        existing = (
            await db.execute(
                select(Payslip).where(Payslip.cycle_id == cycle_id, Payslip.employee_id == employee.id)
            )
        ).scalar_one_or_none()

        # Attendance source: an APPROVED timesheet for this cycle overrides the
        # structure's static lop_days (hourly structures source total hours from
        # it). With no approved timesheet we fall back to struct.lop_days, so
        # behaviour is unchanged until HR adopts timesheets.
        approved_ts = await timesheet_service.get_approved(db, employee.id, cycle_id)
        is_hourly = str(struct.pay_frequency) == PayFrequency.HOURLY.value
        total_hours: Decimal | None = None

        try:
            if is_hourly:
                if approved_ts is None:
                    skipped.append({"employee_id": employee.id, "reason": "no approved timesheet (hourly)"})
                    continue
                total_hours = Decimal(str(approved_ts.total_hours or "0"))
                lop_days = Decimal("0")
                computed = compute_hourly_payslip(
                    struct,
                    total_hours,
                    pt_state=employee.state,
                    adjustments=adjustments_by_employee.get(employee.id),
                    tds_enabled=bool(struct.tds_enabled),
                    tax_profile=tax_profile_by_employee.get(employee.id),
                    statutory=statutory_config,
                )
            else:
                lop_days = (
                    Decimal(str(approved_ts.lop_days or "0"))
                    if approved_ts is not None
                    else Decimal(str(struct.lop_days or "0"))
                )
                computed = compute_payslip(
                    struct,
                    lop_days,
                    working_days,
                    pt_state=employee.state,
                    adjustments=adjustments_by_employee.get(employee.id),
                    tds_enabled=bool(struct.tds_enabled),
                    tax_profile=tax_profile_by_employee.get(employee.id),
                    statutory=statutory_config,
                )
        except ValueError as exc:
            skipped.append({"employee_id": employee.id, "reason": str(exc)})
            continue

        # Record how attendance was derived, for an auditable payslip snapshot.
        snapshot = computed.get("statutory") or {}
        snapshot["attendance"] = {
            "source": "timesheet" if approved_ts is not None else "structure",
            "working_days": float(working_days),
            "lop_days": float(lop_days),
            "total_hours": float(total_hours) if total_hours is not None else None,
            "timesheet_id": str(approved_ts.id) if approved_ts is not None else None,
        }
        computed["statutory"] = snapshot
        # Hourly payslips have no day-proration, so paid_days is not meaningful.
        paid_days = None if is_hourly else computed["paid_days"]

        if existing:
            existing.gross_earnings = computed["gross_earnings"]
            existing.total_deductions = computed["total_deductions"]
            existing.net_pay = computed["net_pay"]
            existing.lop_days = lop_days
            existing.paid_days = paid_days
            existing.earnings = computed["earnings"]
            existing.deductions = computed["deductions"]
            existing.employer_contributions = computed["employer_contributions"]
            existing.statutory = computed["statutory"]
            existing.currency = struct.currency
            existing.status = PayslipStatus.PENDING.value
            updated_count += 1
        else:
            db.add(
                Payslip(
                    company_id=company_id,
                    cycle_id=cycle_id,
                    employee_id=employee.id,
                    gross_earnings=computed["gross_earnings"],
                    total_deductions=computed["total_deductions"],
                    net_pay=computed["net_pay"],
                    lop_days=lop_days,
                    paid_days=paid_days,
                    earnings=computed["earnings"],
                    deductions=computed["deductions"],
                    employer_contributions=computed["employer_contributions"],
                    statutory=computed["statutory"],
                    currency=struct.currency,
                    status=PayslipStatus.PENDING.value,
                )
            )
            created_count += 1

    await db.flush()

    # Roll up totals from all payslips in the cycle.
    all_payslips = (await db.execute(select(Payslip).where(Payslip.cycle_id == cycle_id))).scalars().all()

    total_gross = sum((p.gross_earnings for p in all_payslips), Decimal("0.00"))
    total_ded = sum((p.total_deductions for p in all_payslips), Decimal("0.00"))
    total_net = sum((p.net_pay for p in all_payslips), Decimal("0.00"))
    total_employer = sum(
        (Decimal(str(c.get("amount", 0))) for p in all_payslips for c in (p.employer_contributions or [])),
        Decimal("0.00"),
    )

    cycle.totals = {
        "headcount": len(all_payslips),
        "gross": float(total_gross),
        "deductions": float(total_ded),
        "net": float(total_net),
        # Employer-side statutory cost and total cost-to-company for the cycle.
        "employer_cost": float(total_employer),
        "total_cost": float(total_gross + total_employer),
    }
    cycle.status = PayrollCycleStatus.PROCESSING.value

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    return {"created": created_count, "updated": updated_count, "skipped": skipped}


async def approve_cycle(db: AsyncSession, cycle_id: uuid.UUID, company_id: uuid.UUID) -> PayrollCycle:
    """PROCESSING -> APPROVED (locks payslips from re-run)."""
    cycle = await _load_cycle(db, cycle_id, company_id)
    if cycle.status != PayrollCycleStatus.PROCESSING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cycle must be PROCESSING to approve (is {cycle.status})",
        )
    cycle.status = PayrollCycleStatus.APPROVED.value
    try:
        await db.commit()
        await db.refresh(cycle)
    except Exception:
        await db.rollback()
        raise
    return cycle


async def mark_paid(db: AsyncSession, cycle_id: uuid.UUID, company_id: uuid.UUID) -> PayrollCycle:
    """APPROVED -> PAID. Stamps every payslip paid_at + status PAID."""
    cycle = await _load_cycle(db, cycle_id, company_id)
    if cycle.status != PayrollCycleStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cycle must be APPROVED to mark paid (is {cycle.status})",
        )
    now = datetime.utcnow()
    cycle.status = PayrollCycleStatus.PAID.value
    await db.execute(
        update(Payslip)
        .where(Payslip.cycle_id == cycle_id)
        .values(status=PayslipStatus.PAID.value, paid_at=now)
    )
    try:
        await db.commit()
        await db.refresh(cycle)
    except Exception:
        await db.rollback()
        raise
    return cycle


async def cancel_cycle(db: AsyncSession, cycle_id: uuid.UUID, company_id: uuid.UUID) -> PayrollCycle:
    """Any non-PAID status -> CANCELLED."""
    cycle = await _load_cycle(db, cycle_id, company_id)
    if cycle.status == PayrollCycleStatus.PAID:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A PAID cycle cannot be cancelled")
    cycle.status = PayrollCycleStatus.CANCELLED.value
    try:
        await db.commit()
        await db.refresh(cycle)
    except Exception:
        await db.rollback()
        raise
    return cycle
