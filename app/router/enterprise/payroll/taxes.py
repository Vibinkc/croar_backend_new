import uuid
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.models.enterprise.employee import Employee
from app.models.payroll import PayrollCycle, Payslip
from app.models.payroll.taxes import EmployeeTaxProfile, TdsChallan
from app.payroll.constants import PayrollCycleStatus, Permission
from app.payroll.deps import DBSessionDep, get_current_company_id, require_permission
from app.schemas.enterprise.payroll.taxes import (
    ChallanCreate,
    ChallanOut,
    TaxProfileOut,
    TaxProfileUpsert,
    TdsLiabilityRow,
)

router = APIRouter(prefix="/api/v1/enterprise/taxes", tags=["taxes"])


# ---------------------------------------------------------------------------
# Employee tax profiles (IT declarations)
# ---------------------------------------------------------------------------
@router.get("/profiles", response_model=list[TaxProfileOut])
async def list_tax_profiles(
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> list[EmployeeTaxProfile]:
    """All saved IT declarations for the company (employees without one yet are
    simply absent — the UI shows defaults)."""
    rows = (
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
    return list(rows)


@router.put("/profiles/{employee_id}", response_model=TaxProfileOut)
async def upsert_tax_profile(
    employee_id: uuid.UUID,
    payload: TaxProfileUpsert,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_CONFIGURE)),
) -> EmployeeTaxProfile:
    """Create or replace an employee's IT declaration."""
    employee = (
        await db.execute(
            select(Employee).where(
                Employee.id == employee_id, Employee.company_id == company_id, Employee.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if not employee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")

    profile = (
        await db.execute(
            select(EmployeeTaxProfile).where(
                EmployeeTaxProfile.employee_id == employee_id, EmployeeTaxProfile.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()

    values = payload.model_dump()
    values["tax_regime"] = payload.tax_regime.value
    if profile is None:
        profile = EmployeeTaxProfile(company_id=company_id, employee_id=employee_id, **values)
        db.add(profile)
    else:
        for field, value in values.items():
            setattr(profile, field, value)
    try:
        await db.commit()
        await db.refresh(profile)
    except Exception:
        await db.rollback()
        raise
    return profile


# ---------------------------------------------------------------------------
# TDS challans
# ---------------------------------------------------------------------------
@router.get("/challans", response_model=list[ChallanOut])
async def list_challans(
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> list[TdsChallan]:
    rows = (
        (
            await db.execute(
                select(TdsChallan)
                .where(TdsChallan.company_id == company_id, TdsChallan.deleted_at.is_(None))
                .order_by(TdsChallan.deposit_date.desc(), TdsChallan.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


@router.get("/tds-liabilities", response_model=list[TdsLiabilityRow])
async def tds_liabilities(
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> list[TdsLiabilityRow]:
    """Per-month TDS withheld (from payslips) vs deposited (from challans)."""
    # TDS withheld: sum the "TDS" deduction line on each payslip, grouped by the
    # cycle's period month. Cancelled cycles are excluded.
    rows = (
        await db.execute(
            select(Payslip, PayrollCycle.period_start)
            .join(PayrollCycle, Payslip.cycle_id == PayrollCycle.id)
            .where(
                Payslip.company_id == company_id,
                PayrollCycle.deleted_at.is_(None),
                PayrollCycle.status != PayrollCycleStatus.CANCELLED.value,
            )
        )
    ).all()
    deducted: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for payslip, period_start in rows:
        month = f"{period_start.year:04d}-{period_start.month:02d}"
        for line in payslip.deductions or []:
            if line.get("code") == "TDS":
                deducted[month] += Decimal(str(line.get("amount") or "0"))

    # TDS deposited: sum challan amounts per period month.
    challans = (
        (
            await db.execute(
                select(TdsChallan).where(TdsChallan.company_id == company_id, TdsChallan.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    deposited: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for challan in challans:
        deposited[challan.period_month] += challan.amount

    months = sorted(set(deducted) | set(deposited), reverse=True)
    return [
        TdsLiabilityRow(
            period_month=m,
            tds_deducted=deducted.get(m, Decimal("0")),
            tds_deposited=deposited.get(m, Decimal("0")),
            difference=deducted.get(m, Decimal("0")) - deposited.get(m, Decimal("0")),
        )
        for m in months
    ]


@router.post("/challans", response_model=ChallanOut, status_code=status.HTTP_201_CREATED)
async def create_challan(
    payload: ChallanCreate,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_CONFIGURE)),
) -> TdsChallan:
    challan = TdsChallan(company_id=company_id, **payload.model_dump())
    db.add(challan)
    try:
        await db.commit()
        await db.refresh(challan)
    except Exception:
        await db.rollback()
        raise
    return challan


@router.delete("/challans/{id}", response_model=ChallanOut)
async def delete_challan(
    id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_CONFIGURE)),
) -> TdsChallan:
    challan = (
        await db.execute(
            select(TdsChallan).where(
                TdsChallan.id == id, TdsChallan.company_id == company_id, TdsChallan.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if not challan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Challan not found")
    challan.deleted_at = datetime.now(UTC).replace(tzinfo=None)
    try:
        await db.commit()
        await db.refresh(challan)
    except Exception:
        await db.rollback()
        raise
    return challan
