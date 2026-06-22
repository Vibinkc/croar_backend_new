import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.enterprise.company import Company
from app.models.enterprise.employee import Employee
from app.models.payroll import PayrollAdjustment, PayrollCycle, Payslip, SalaryStructure, SalaryTemplate
from app.payroll.constants import DEFAULT_WORKING_DAYS, PayrollCycleStatus, Permission
from app.payroll.deps import DBSessionDep, get_current_company_id, require_permission
from app.schemas.enterprise.payroll.payroll import (
    AdjustmentCreate,
    AdjustmentOut,
    BulkEmailResult,
    DashboardSummary,
    EmailResult,
    PayrollCycleCreate,
    PayrollCycleOut,
    PayslipDetailOut,
    PayslipOut,
    RunResult,
    SalaryStructureCreate,
    SalaryStructureOut,
    SalaryStructureUpdate,
    SalaryTemplateCreate,
    SalaryTemplateOut,
    SalaryTemplateUpdate,
    StructurePreviewIn,
    StructurePreviewOut,
    TemplateApplyIn,
    TemplateApplyResult,
)
from app.schemas.enterprise.payroll.settings import PayslipSettings
from app.services.payroll import docx_service, email_service, payroll_service, pdf_service

router = APIRouter(prefix="/api/v1/enterprise/payroll", tags=["payroll"])


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@router.get("/dashboard", response_model=DashboardSummary)
async def get_dashboard(
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> dict:
    """Application-wide overview (employees, structures, cycles, money)."""
    return await payroll_service.dashboard_summary(db, company_id)


# ---------------------------------------------------------------------------
# Salary structures
# ---------------------------------------------------------------------------
@router.post("/structures", response_model=SalaryStructureOut, status_code=status.HTTP_201_CREATED)
async def create_salary_structure(
    payload: SalaryStructureCreate,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_CONFIGURE)),
) -> SalaryStructure:
    if payload.is_active:
        existing = (
            await db.execute(
                select(SalaryStructure).where(
                    SalaryStructure.employee_id == payload.employee_id,
                    SalaryStructure.company_id == company_id,
                    SalaryStructure.is_active.is_(True),
                    SalaryStructure.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Employee already has an active salary structure.",
            )

    struct = SalaryStructure(
        company_id=company_id,
        employee_id=payload.employee_id,
        ctc=payload.ctc,
        currency=payload.currency,
        pay_frequency=payload.pay_frequency.value,
        hourly_rate=payload.hourly_rate,
        effective_from=payload.effective_from,
        components=[c.model_dump(mode="json") for c in payload.components],
        default_deductions=[d.model_dump(mode="json") for d in payload.default_deductions],
        lop_days=payload.lop_days,
        is_active=payload.is_active,
        pf_enabled=payload.pf_enabled,
        pf_cap_at_ceiling=payload.pf_cap_at_ceiling,
        pf_wage_codes=payload.pf_wage_codes,
        esi_enabled=payload.esi_enabled,
        pt_enabled=payload.pt_enabled,
        tds_enabled=payload.tds_enabled,
    )
    db.add(struct)
    try:
        await db.commit()
        await db.refresh(struct)
    except Exception:
        await db.rollback()
        raise
    return struct


@router.post("/structures/preview", response_model=StructurePreviewOut)
async def preview_salary_structure(
    payload: StructurePreviewIn,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> dict:
    """Live, non-persisted payslip calculation for the structure form.

    Returns the exact figures a payroll run would produce — including statutory
    (PF/ESI/PT) and TDS — so the form's estimate reflects real deductions, not
    just the manual lines.
    """
    draft = {
        "employee_id": payload.employee_id,
        "ctc": payload.ctc,
        "pay_frequency": payload.pay_frequency.value,
        "components": [c.model_dump(mode="json") for c in payload.components],
        "default_deductions": [d.model_dump(mode="json") for d in payload.default_deductions],
        "lop_days": payload.lop_days,
        "pf_enabled": payload.pf_enabled,
        "pf_cap_at_ceiling": payload.pf_cap_at_ceiling,
        "pf_wage_codes": payload.pf_wage_codes,
        "esi_enabled": payload.esi_enabled,
        "pt_enabled": payload.pt_enabled,
        "tds_enabled": payload.tds_enabled,
    }
    return await payroll_service.preview_structure(db, company_id, draft)


@router.get("/structures", response_model=list[SalaryStructureOut])
async def list_salary_structures(
    db: DBSessionDep,
    employee_id: uuid.UUID | None = None,
    active_only: bool = True,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> list[SalaryStructure]:
    """List salary structures. Defaults to ``active_only`` so replaced/superseded
    structures (deactivated on edit or template re-apply) don't show up as
    duplicate rows for the same employee. Pass ``active_only=false`` for history."""
    stmt = select(SalaryStructure).where(
        SalaryStructure.company_id == company_id, SalaryStructure.deleted_at.is_(None)
    )
    if active_only:
        stmt = stmt.where(SalaryStructure.is_active.is_(True))
    if employee_id is not None:
        stmt = stmt.where(SalaryStructure.employee_id == employee_id)
    rows = (await db.execute(stmt)).scalars().all()
    return list(rows)


@router.get("/structures/{id}", response_model=SalaryStructureOut)
async def get_salary_structure(
    id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> SalaryStructure:
    struct = (
        await db.execute(
            select(SalaryStructure).where(
                SalaryStructure.id == id,
                SalaryStructure.company_id == company_id,
                SalaryStructure.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if not struct:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salary structure not found")
    return struct


@router.put("/structures/{id}", response_model=SalaryStructureOut)
async def update_salary_structure(
    id: uuid.UUID,
    payload: SalaryStructureUpdate,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_CONFIGURE)),
) -> SalaryStructure:
    struct = (
        await db.execute(
            select(SalaryStructure).where(
                SalaryStructure.id == id,
                SalaryStructure.company_id == company_id,
                SalaryStructure.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if not struct:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salary structure not found")

    if payload.is_active is True and struct.is_active is not True:
        clash = (
            await db.execute(
                select(SalaryStructure).where(
                    SalaryStructure.employee_id == struct.employee_id,
                    SalaryStructure.company_id == company_id,
                    SalaryStructure.is_active.is_(True),
                    SalaryStructure.deleted_at.is_(None),
                    SalaryStructure.id != id,
                )
            )
        ).scalar_one_or_none()
        if clash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Employee already has an active salary structure.",
            )

    update_fields = payload.model_dump(exclude_unset=True)
    if payload.components is not None:
        update_fields["components"] = [c.model_dump(mode="json") for c in payload.components]
    if payload.default_deductions is not None:
        update_fields["default_deductions"] = [d.model_dump(mode="json") for d in payload.default_deductions]
    if payload.pay_frequency is not None:
        update_fields["pay_frequency"] = payload.pay_frequency.value

    for field, value in update_fields.items():
        setattr(struct, field, value)
    try:
        await db.commit()
        await db.refresh(struct)
    except Exception:
        await db.rollback()
        raise
    return struct


@router.delete("/structures/{id}", response_model=SalaryStructureOut)
async def delete_salary_structure(
    id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_CONFIGURE)),
) -> SalaryStructure:
    struct = (
        await db.execute(
            select(SalaryStructure).where(
                SalaryStructure.id == id,
                SalaryStructure.company_id == company_id,
                SalaryStructure.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if not struct:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salary structure not found")
    struct.deleted_at = datetime.utcnow()
    struct.is_active = False
    try:
        await db.commit()
        await db.refresh(struct)
    except Exception:
        await db.rollback()
        raise
    return struct


# ---------------------------------------------------------------------------
# Salary templates (reusable, CTC-driven; apply -> per-employee structures)
# ---------------------------------------------------------------------------
def _template_values(payload: SalaryTemplateCreate | SalaryTemplateUpdate) -> dict:
    """Serialise template fields set on the payload (MoneyLines -> JSON)."""
    data = payload.model_dump(exclude_unset=True)
    if "pay_frequency" in data and data["pay_frequency"] is not None:
        data["pay_frequency"] = payload.pay_frequency.value
    for key in ("components", "default_deductions"):
        if data.get(key) is not None:
            data[key] = [line.model_dump(mode="json") for line in getattr(payload, key)]
    return data


async def _load_template(db: DBSessionDep, id: uuid.UUID, company_id: uuid.UUID) -> SalaryTemplate:
    template = (
        await db.execute(
            select(SalaryTemplate).where(
                SalaryTemplate.id == id,
                SalaryTemplate.company_id == company_id,
                SalaryTemplate.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    return template


@router.post("/templates", response_model=SalaryTemplateOut, status_code=status.HTTP_201_CREATED)
async def create_template(
    payload: SalaryTemplateCreate,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_CONFIGURE)),
) -> SalaryTemplate:
    template = SalaryTemplate(company_id=company_id, **_template_values(payload))
    db.add(template)
    try:
        await db.commit()
        await db.refresh(template)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="A template with this name already exists."
        )
    except Exception:
        await db.rollback()
        raise
    return template


@router.get("/templates", response_model=list[SalaryTemplateOut])
async def list_templates(
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> list[SalaryTemplate]:
    rows = (
        (
            await db.execute(
                select(SalaryTemplate)
                .where(SalaryTemplate.company_id == company_id, SalaryTemplate.deleted_at.is_(None))
                .order_by(SalaryTemplate.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


@router.get("/templates/{id}", response_model=SalaryTemplateOut)
async def get_template(
    id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> SalaryTemplate:
    return await _load_template(db, id, company_id)


@router.put("/templates/{id}", response_model=SalaryTemplateOut)
async def update_template(
    id: uuid.UUID,
    payload: SalaryTemplateUpdate,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_CONFIGURE)),
) -> SalaryTemplate:
    template = await _load_template(db, id, company_id)
    for field, value in _template_values(payload).items():
        setattr(template, field, value)
    try:
        await db.commit()
        await db.refresh(template)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="A template with this name already exists."
        )
    except Exception:
        await db.rollback()
        raise
    return template


@router.delete("/templates/{id}", response_model=SalaryTemplateOut)
async def delete_template(
    id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_CONFIGURE)),
) -> SalaryTemplate:
    template = await _load_template(db, id, company_id)
    template.deleted_at = datetime.utcnow()
    try:
        await db.commit()
        await db.refresh(template)
    except Exception:
        await db.rollback()
        raise
    return template


@router.post("/templates/{id}/apply", response_model=TemplateApplyResult)
async def apply_template(
    id: uuid.UUID,
    payload: TemplateApplyIn,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_CONFIGURE)),
) -> dict:
    """Generate (or replace) salary structures for the given employees from this
    template. Each employee's structure is stamped with their own CTC, so the
    template's percentage rules scale per person."""
    return await payroll_service.apply_template(
        db, company_id, id, [a.model_dump() for a in payload.assignments], payload.replace_existing
    )


# ---------------------------------------------------------------------------
# Payroll cycles
# ---------------------------------------------------------------------------
@router.post("/cycles", response_model=PayrollCycleOut, status_code=status.HTTP_201_CREATED)
async def create_payroll_cycle(
    payload: PayrollCycleCreate,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_CONFIGURE)),
) -> PayrollCycle:
    cycle = PayrollCycle(
        company_id=company_id,
        name=payload.name,
        period_start=payload.period_start,
        period_end=payload.period_end,
        pay_date=payload.pay_date,
        notes=payload.notes,
        status=PayrollCycleStatus.DRAFT.value,
        totals={},
    )
    db.add(cycle)
    try:
        await db.commit()
        await db.refresh(cycle)
    except Exception:
        await db.rollback()
        raise
    return cycle


@router.get("/cycles", response_model=list[PayrollCycleOut])
async def list_payroll_cycles(
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> list[PayrollCycle]:
    rows = (
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
    return list(rows)


@router.get("/cycles/{id}", response_model=PayrollCycleOut)
async def get_payroll_cycle(
    id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> PayrollCycle:
    return await payroll_service._load_cycle(db, id, company_id)


@router.post("/cycles/{id}/run", response_model=RunResult)
async def run_payroll_cycle(
    id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_RUN)),
) -> dict:
    return await payroll_service.run_payroll(db, id, company_id)


@router.post("/cycles/{id}/approve", response_model=PayrollCycleOut)
async def approve_payroll_cycle(
    id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_APPROVE)),
) -> PayrollCycle:
    return await payroll_service.approve_cycle(db, id, company_id)


@router.post("/cycles/{id}/mark-paid", response_model=PayrollCycleOut)
async def mark_payroll_cycle_paid(
    id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_PAY)),
) -> PayrollCycle:
    return await payroll_service.mark_paid(db, id, company_id)


@router.post("/cycles/{id}/cancel", response_model=PayrollCycleOut)
async def cancel_payroll_cycle(
    id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_MANAGE)),
) -> PayrollCycle:
    return await payroll_service.cancel_cycle(db, id, company_id)


@router.delete("/cycles/{id}", response_model=PayrollCycleOut)
async def delete_payroll_cycle(
    id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_MANAGE)),
) -> PayrollCycle:
    """Soft-delete a cycle. Allowed for any status except PAID (spec §6)."""
    cycle = await payroll_service._load_cycle(db, id, company_id)
    if cycle.status == PayrollCycleStatus.PAID:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A PAID cycle cannot be deleted")
    cycle.deleted_at = datetime.utcnow()
    try:
        await db.commit()
        await db.refresh(cycle)
    except Exception:
        await db.rollback()
        raise
    return cycle


# ---------------------------------------------------------------------------
# Per-run adjustments (one-time earnings/deductions for a cycle)
# ---------------------------------------------------------------------------
_EDITABLE_CYCLE_STATUSES = (PayrollCycleStatus.DRAFT, PayrollCycleStatus.PROCESSING)


@router.get("/cycles/{id}/adjustments", response_model=list[AdjustmentOut])
async def list_cycle_adjustments(
    id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> list[PayrollAdjustment]:
    await payroll_service._load_cycle(db, id, company_id)  # 404s if not in company
    rows = (
        (
            await db.execute(
                select(PayrollAdjustment)
                .where(
                    PayrollAdjustment.cycle_id == id,
                    PayrollAdjustment.company_id == company_id,
                    PayrollAdjustment.deleted_at.is_(None),
                )
                .order_by(PayrollAdjustment.created_at)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


@router.post("/cycles/{id}/adjustments", response_model=AdjustmentOut, status_code=status.HTTP_201_CREATED)
async def add_cycle_adjustment(
    id: uuid.UUID,
    payload: AdjustmentCreate,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_CONFIGURE)),
) -> PayrollAdjustment:
    """Attach a one-time earning/deduction to a cycle. Re-run the cycle for it to
    take effect. Allowed only while the cycle is DRAFT or PROCESSING."""
    cycle = await payroll_service._load_cycle(db, id, company_id)
    if cycle.status not in _EDITABLE_CYCLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Adjustments can only be changed while the cycle is DRAFT or PROCESSING (is {cycle.status}).",
        )
    employee = (
        await db.execute(
            select(Employee).where(
                Employee.id == payload.employee_id,
                Employee.company_id == company_id,
                Employee.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if not employee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")

    adjustment = PayrollAdjustment(
        company_id=company_id,
        cycle_id=id,
        employee_id=payload.employee_id,
        kind=payload.kind.value,
        code=payload.code,
        label=payload.label,
        amount=payload.amount,
        note=payload.note,
    )
    db.add(adjustment)
    try:
        await db.commit()
        await db.refresh(adjustment)
    except Exception:
        await db.rollback()
        raise
    return adjustment


@router.delete("/adjustments/{id}", response_model=AdjustmentOut)
async def delete_adjustment(
    id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_CONFIGURE)),
) -> PayrollAdjustment:
    """Soft-delete an adjustment. Allowed only while its cycle is DRAFT/PROCESSING."""
    adjustment = (
        await db.execute(
            select(PayrollAdjustment).where(
                PayrollAdjustment.id == id,
                PayrollAdjustment.company_id == company_id,
                PayrollAdjustment.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if not adjustment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Adjustment not found")
    cycle = await payroll_service._load_cycle(db, adjustment.cycle_id, company_id)
    if cycle.status not in _EDITABLE_CYCLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Adjustments can only be changed while the cycle is DRAFT or PROCESSING (is {cycle.status}).",
        )
    adjustment.deleted_at = datetime.utcnow()
    try:
        await db.commit()
        await db.refresh(adjustment)
    except Exception:
        await db.rollback()
        raise
    return adjustment


# ---------------------------------------------------------------------------
# Payslips
# ---------------------------------------------------------------------------
@router.get("/cycles/{id}/payslips", response_model=list[PayslipOut])
async def list_cycle_payslips(
    id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> list[Payslip]:
    cycle = await payroll_service._load_cycle(db, id, company_id)
    # The computed payslip summary (names + amounts) is visible once a run has
    # generated payslips — i.e. any time after DRAFT and before CANCELLED. The
    # full individual payslip (detail/PDF) stays gated to PAID (see get_payslip).
    if cycle.status in (PayrollCycleStatus.DRAFT, PayrollCycleStatus.CANCELLED):
        return []
    rows = (
        (await db.execute(select(Payslip).where(Payslip.cycle_id == id, Payslip.company_id == company_id)))
        .scalars()
        .all()
    )
    return list(rows)


@router.get("/payslips/{id}", response_model=PayslipDetailOut)
async def get_payslip(
    id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> Payslip:
    payslip = (
        await db.execute(select(Payslip).where(Payslip.id == id, Payslip.company_id == company_id))
    ).scalar_one_or_none()
    if not payslip:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payslip not found")
    # Payslips are only released once the cycle has been disbursed (marked PAID).
    cycle = await payroll_service._load_cycle(db, payslip.cycle_id, company_id)
    if cycle.status != PayrollCycleStatus.PAID:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This payslip is not available until the payroll cycle is marked as paid.",
        )
    return payslip


# ---------------------------------------------------------------------------
# Payslip PDF + email
# ---------------------------------------------------------------------------
async def _gather_payslip(
    db: DBSessionDep, payslip_id: uuid.UUID, company_id: uuid.UUID
) -> tuple[Payslip, PayrollCycle, Employee | None, Company | None]:
    """Load a payslip + its cycle/employee/company, enforcing the PAID gate.

    Mirrors ``get_payslip``: a payslip is only retrievable (and thus printable
    or emailable) once its cycle has been marked PAID.
    """
    payslip = (
        await db.execute(select(Payslip).where(Payslip.id == payslip_id, Payslip.company_id == company_id))
    ).scalar_one_or_none()
    if not payslip:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payslip not found")
    cycle = await payroll_service._load_cycle(db, payslip.cycle_id, company_id)
    if cycle.status != PayrollCycleStatus.PAID:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This payslip is not available until the payroll cycle is marked as paid.",
        )
    employee = (
        await db.execute(select(Employee).where(Employee.id == payslip.employee_id))
    ).scalar_one_or_none()
    company = (await db.execute(select(Company).where(Company.id == company_id))).scalar_one_or_none()
    return payslip, cycle, employee, company


def _payslip_working_days(payslip: Payslip) -> int:
    """The working-day basis captured on the payslip at run time (attendance
    snapshot), falling back to the fixed default for pre-timesheets payslips."""
    try:
        wd = (payslip.statutory or {}).get("attendance", {}).get("working_days")
        if wd:
            return int(round(float(wd)))
    except (AttributeError, TypeError, ValueError):
        pass
    return DEFAULT_WORKING_DAYS


def _render_pdf(
    payslip: Payslip, cycle: PayrollCycle, employee: Employee | None, company: Company | None
) -> bytes:
    emp_name = f"{employee.first_name} {employee.last_name}".strip() if employee else str(payslip.employee_id)
    ps = PayslipSettings.from_stored(company.payslip_settings if company else None)
    company_label = ps.display_name or (company.name if company else "Company")
    return pdf_service.render_payslip_pdf(
        company_name=company_label,
        employee_name=emp_name,
        employee_email=employee.email if employee else "",
        ref=str(payslip.id)[:8],
        period_start=cycle.period_start,
        period_end=cycle.period_end,
        pay_date=cycle.pay_date,
        status=payslip.status,
        earnings=payslip.earnings or [],
        deductions=payslip.deductions or [],
        gross=payslip.gross_earnings,
        total_deductions=payslip.total_deductions,
        net=payslip.net_pay,
        lop_days=payslip.lop_days,
        paid_days=payslip.paid_days,
        working_days=_payslip_working_days(payslip),
        currency=payslip.currency,
        employer_contributions=payslip.employer_contributions or [],
        accent_color=ps.accent_color,
        footer_note=ps.footer_note,
        logo_url=ps.logo_url,
        show_employer_contributions=ps.show_employer_contributions,
        show_attendance=ps.show_attendance,
    )


def _pdf_filename(cycle: PayrollCycle, employee: Employee | None, payslip: Payslip) -> str:
    who = (
        f"{employee.first_name}-{employee.last_name}".strip("-") if employee else str(payslip.employee_id)[:8]
    )
    safe_who = "".join(c if c.isalnum() or c in "-_" else "-" for c in who) or "employee"
    safe_cycle = "".join(c if c.isalnum() or c in "-_" else "-" for c in cycle.name) or "cycle"
    return f"payslip-{safe_who}-{safe_cycle}.pdf"


def _docx_context(
    payslip: Payslip, cycle: PayrollCycle, employee: Employee | None, company: Company | None
) -> dict:
    """Build the token context handed to an uploaded .docx template."""
    ps = PayslipSettings.from_stored(company.payslip_settings if company else None)
    cur = payslip.currency

    def money(v: object) -> str:
        return f"{cur} {float(v or 0):,.2f}"

    def lines(rows: list[dict] | None) -> list[dict]:
        return [
            {
                "code": r.get("code", ""),
                "label": r.get("label") or r.get("code", ""),
                "amount": money(r.get("amount")),
                "amount_raw": float(r.get("amount") or 0),
            }
            for r in (rows or [])
        ]

    emp_name = f"{employee.first_name} {employee.last_name}".strip() if employee else str(payslip.employee_id)

    def block(rows: list[dict]) -> str:
        # Preformatted "label<TAB>amount" lines; docxtpl turns the newlines into
        # real Word line breaks, so `{{ earnings_lines }}` just works.
        return "\n".join(f"{r['label']}\t{r['amount']}" for r in rows) or "—"

    earnings = lines(payslip.earnings)
    deductions = lines(payslip.deductions)
    employer = lines(payslip.employer_contributions)

    # Code-keyed maps so fixed-layout templates can place a specific component
    # in a specific cell, e.g. {{ amount.BASIC }}, {{ amount.HRA }}, {{ amount.PF }},
    # {{ amount.TDS }}. Missing codes render as empty (Jinja Undefined).
    amount: dict[str, str] = {}
    amount_raw: dict[str, float] = {}
    for r in earnings + deductions + employer:
        amount[r["code"]] = r["amount"]
        amount_raw[r["code"]] = r["amount_raw"]

    addr_parts = []
    if company:
        addr_parts = [
            getattr(company, p, None)
            for p in ("address_line1", "address_line2", "city", "state", "pincode", "country")
        ]
    company_address = ", ".join(p for p in addr_parts if p)

    doj = getattr(employee, "date_of_joining", None) if employee else None
    company_display = ps.display_name or (company.name if company else "Company")
    return {
        "company_name": company_display,
        # Logo placeholder ("[ COMPANY LOGO ]"). Text fallback = company display
        # name; replaced by the real logo IMAGE in render_payslip_doc when the
        # branding logo_url is set.
        "logo": company_display,
        "company": {
            "name": company.name if company else "",
            "legal_name": (getattr(company, "legal_name", None) or "") if company else "",
            "address": company_address,
            "city": (getattr(company, "city", None) or "") if company else "",
            "state": (getattr(company, "state", None) or "") if company else "",
            "pincode": (getattr(company, "pincode", None) or "") if company else "",
            "contact_email": (getattr(company, "contact_email", None) or "") if company else "",
            "contact_phone": (getattr(company, "contact_phone", None) or "") if company else "",
            "pan": (getattr(company, "pan", None) or "") if company else "",
            "tan": (getattr(company, "tan", None) or "") if company else "",
        },
        "employee": {
            "name": emp_name,
            "email": (employee.email if employee else "") or "",
            "code": (getattr(employee, "employee_id", None) or "") if employee else "",
            "designation": (getattr(employee, "designation", None) or "") if employee else "",
            "department": (getattr(employee, "department_name", None) or "") if employee else "",
            "location": (getattr(employee, "location", None) or "") if employee else "",
            "bank_account_no": (getattr(employee, "bank_account_no", None) or "") if employee else "",
            "pan": (getattr(employee, "pan", None) or "") if employee else "",
            "uan": (getattr(employee, "uan", None) or "") if employee else "",
            "esic": (getattr(employee, "esic_number", None) or "") if employee else "",
            "state": (getattr(employee, "state", None) or "") if employee else "",
            "date_of_joining": str(doj) if doj else "",
        },
        "ref": str(payslip.id)[:8],
        "status": payslip.status,
        "currency": cur,
        "cycle_name": cycle.name,
        "period_start": str(cycle.period_start),
        "period_end": str(cycle.period_end),
        "pay_date": str(cycle.pay_date),
        "earnings": earnings,
        "deductions": deductions,
        "employer_contributions": employer,
        # Preformatted, multi-line versions for simple templates.
        "earnings_lines": block(earnings),
        "deductions_lines": block(deductions),
        "employer_contributions_lines": block(employer),
        # Code-keyed amounts for fixed-layout templates.
        "amount": amount,
        "amount_raw": amount_raw,
        "gross": money(payslip.gross_earnings),
        "total_deductions": money(payslip.total_deductions),
        "net": money(payslip.net_pay),
        # Net pay spelled out, e.g. "Rupees Twelve Thousand … Only" — for the
        # "Net Pay in words" line many company payslips carry.
        "net_in_words": docx_service.amount_to_words(payslip.net_pay, cur),
        "gross_in_words": docx_service.amount_to_words(payslip.gross_earnings, cur),
        "gross_raw": float(payslip.gross_earnings),
        "net_raw": float(payslip.net_pay),
        "lop_days": float(payslip.lop_days or 0),
        "paid_days": float(payslip.paid_days or 0),
        "working_days": _payslip_working_days(payslip),
    }


def _render_payslip_doc(
    payslip: Payslip, cycle: PayrollCycle, employee: Employee | None, company: Company | None
) -> bytes | None:
    """Fill the company's uploaded .docx template, or None if there isn't one."""
    if not company or not company.payslip_doc_template:
        return None
    ps = PayslipSettings.from_stored(company.payslip_settings if company else None)
    ctx = _docx_context(payslip, cycle, employee, company)
    # Embed the company logo (from the branding logo_url) as a real image for the
    # {{ logo }} token; falls back to the company-name text when none is set.
    logo = docx_service.fetch_logo_image(ps.logo_url) if ps.logo_url else None
    return docx_service.render_payslip_docx(company.payslip_doc_template, ctx, logo_image=logo)


def _render_template_html(
    payslip: Payslip, cycle: PayrollCycle, employee: Employee | None, company: Company | None
) -> str | None:
    """The company's template, filled and rendered to an HTML fragment — used for
    the on-screen / print payslip view. None when no template is enabled."""
    ps = PayslipSettings.from_stored(company.payslip_settings if company else None)
    if not (company and company.payslip_doc_template and ps.use_doc_template):
        return None
    docx_bytes = _render_payslip_doc(payslip, cycle, employee, company)
    if not docx_bytes:
        return None
    return docx_service.docx_to_html(docx_bytes)


def _payslip_pdf_bytes(
    payslip: Payslip, cycle: PayrollCycle, employee: Employee | None, company: Company | None
) -> bytes:
    """The payslip PDF. When the company's .docx template is enabled it drives
    the PDF: via LibreOffice/Word for full fidelity when available, otherwise via
    fpdf2's HTML engine (so the template still applies without any converter).
    Falls back to the built-in fpdf2 layout only if the template path fails."""
    ps = PayslipSettings.from_stored(company.payslip_settings if company else None)
    if company and company.payslip_doc_template and ps.use_doc_template:
        try:
            docx_bytes = _render_payslip_doc(payslip, cycle, employee, company)
            if docx_bytes:
                pdf = docx_service.docx_to_pdf(docx_bytes)  # LibreOffice/Word
                if pdf:
                    return pdf
                # No converter installed — render the template via HTML instead.
                html = docx_service.docx_to_html(docx_bytes)
                return pdf_service.render_template_html_pdf(html, ps.footer_note)
        except Exception:
            pass  # any template failure -> built-in layout
    return _render_pdf(payslip, cycle, employee, company)


@router.get("/payslips/{id}/pdf")
async def download_payslip_pdf(
    id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> Response:
    """Download the payslip as a server-rendered PDF (same artifact emailed).

    Uses the company's uploaded .docx template when enabled and a docx->pdf
    converter is available; otherwise the built-in layout."""
    payslip, cycle, employee, company = await _gather_payslip(db, id, company_id)
    pdf = await run_in_threadpool(_payslip_pdf_bytes, payslip, cycle, employee, company)
    filename = _pdf_filename(cycle, employee, payslip)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # Never cache: the template/branding can change, so each download
            # must reflect the current state (avoids the browser re-serving a
            # previously downloaded, now-stale PDF).
            "Cache-Control": "no-store",
        },
    )


@router.get("/payslips/{id}/docx")
async def download_payslip_docx(
    id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> Response:
    """Download the payslip as a filled Word document from the company's uploaded
    template. 404 if no template has been uploaded."""
    payslip, cycle, employee, company = await _gather_payslip(db, id, company_id)
    if not company or not company.payslip_doc_template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No payslip document template has been uploaded."
        )
    try:
        docx_bytes = await run_in_threadpool(_render_payslip_doc, payslip, cycle, employee, company)
    except Exception as exc:  # template authoring error (bad token, etc.)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Could not fill the template: {exc}"
        )
    filename = _pdf_filename(cycle, employee, payslip)[:-4] + ".docx"
    return Response(
        content=docx_bytes or b"",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


@router.get("/payslips/{id}/preview-html")
async def payslip_preview_html(
    id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> dict[str, str | None]:
    """HTML for the on-screen / print payslip, rendered from the company's
    uploaded template when one is enabled. ``{"html": null}`` means there's no
    active template, so the client should show its built-in layout."""
    payslip, cycle, employee, company = await _gather_payslip(db, id, company_id)
    try:
        html = await run_in_threadpool(_render_template_html, payslip, cycle, employee, company)
    except Exception:
        html = None  # any template failure -> client falls back to built-in view
    return {"html": html}


@router.post("/payslips/{id}/email", response_model=EmailResult)
async def email_payslip(
    id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_PAY)),
) -> EmailResult:
    """Email the payslip (with PDF attached) to the employee's address."""
    payslip, cycle, employee, company = await _gather_payslip(db, id, company_id)
    if not employee or not employee.email:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="This employee has no email address on file.",
        )
    pdf = await run_in_threadpool(_payslip_pdf_bytes, payslip, cycle, employee, company)
    company_name = company.name if company else "Croar Payroll"
    html = email_service.payslip_email_html(
        employee_name=f"{employee.first_name} {employee.last_name}".strip(),
        company_name=company_name,
        period=f"{cycle.period_start} to {cycle.period_end}",
        net_pay=f"{payslip.currency} {float(payslip.net_pay):,.2f}",
    )
    try:
        await run_in_threadpool(
            email_service.send_payslip_email,
            to_email=employee.email,
            subject=f"Your payslip — {cycle.name}",
            html=html,
            pdf_bytes=pdf,
            filename=_pdf_filename(cycle, employee, payslip),
        )
    except email_service.EmailNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except Exception as exc:  # surface any SDK/network failure as 502
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Failed to send email: {exc}"
        ) from exc
    return EmailResult(sent=True, to=employee.email)


@router.post("/cycles/{id}/email-payslips", response_model=BulkEmailResult)
async def email_cycle_payslips(
    id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_PAY)),
) -> BulkEmailResult:
    """Email every payslip in a PAID cycle to its employee.

    Best-effort: each payslip is attempted independently; failures (no email,
    send error) are collected and returned rather than aborting the batch.
    """
    cycle = await payroll_service._load_cycle(db, id, company_id)
    if cycle.status != PayrollCycleStatus.PAID:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Payslips can only be emailed once the cycle is marked as paid.",
        )
    if not email_service.is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email is not configured. Set SMTP_USERNAME, SMTP_PASSWORD and SMTP_FROM_EMAIL.",
        )

    payslips = (
        (await db.execute(select(Payslip).where(Payslip.cycle_id == id, Payslip.company_id == company_id)))
        .scalars()
        .all()
    )
    company = (await db.execute(select(Company).where(Company.id == company_id))).scalar_one_or_none()
    company_name = company.name if company else "Croar Payroll"

    sent = 0
    failed: list[dict[str, object]] = []
    for slip in payslips:
        employee = (
            await db.execute(select(Employee).where(Employee.id == slip.employee_id))
        ).scalar_one_or_none()
        if not employee or not employee.email:
            failed.append(
                {"payslip_id": slip.id, "employee_id": slip.employee_id, "reason": "no email address on file"}
            )
            continue
        pdf = await run_in_threadpool(_render_pdf, slip, cycle, employee, company)
        html = email_service.payslip_email_html(
            employee_name=f"{employee.first_name} {employee.last_name}".strip(),
            company_name=company_name,
            period=f"{cycle.period_start} to {cycle.period_end}",
            net_pay=f"{slip.currency} {float(slip.net_pay):,.2f}",
        )
        try:
            await run_in_threadpool(
                email_service.send_payslip_email,
                to_email=employee.email,
                subject=f"Your payslip — {cycle.name}",
                html=html,
                pdf_bytes=pdf,
                filename=_pdf_filename(cycle, employee, slip),
            )
            sent += 1
        except Exception as exc:  # collect per-payslip failures
            failed.append({"payslip_id": slip.id, "employee_id": slip.employee_id, "reason": str(exc)})
    return BulkEmailResult.model_validate({"sent": sent, "failed": failed})
