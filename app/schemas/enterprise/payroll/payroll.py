import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.payroll.constants import AdjustmentKind, LineType, PayFrequency, PayrollCycleStatus, PayslipStatus


# ---------------------------------------------------------------------------
# Money line (shared union for earnings & deductions)
# ---------------------------------------------------------------------------
class MoneyLine(BaseModel):
    """A single earning or deduction line.

    type="fixed"   -> requires `amount` (monthly absolute value)
    type="percent" -> requires `percent`; `percent_of` references another line's
                      code, the reserved code "CTC" (per-period cost-to-company),
                      or is omitted to mean "of gross".
    type="balance" -> no amount/percent; resolves to whatever CTC is left after
                      the other earnings (period_CTC - sum(others)). Earnings
                      only — keeps a CTC-driven template summing to exactly CTC.
    """

    code: str = Field(..., min_length=1, max_length=64)
    label: str = Field(..., min_length=1, max_length=120)
    type: LineType
    amount: Decimal | None = None
    percent: Decimal | None = None
    percent_of: str | None = None

    @model_validator(mode="after")
    def _check_shape(self) -> "MoneyLine":
        if self.type == LineType.FIXED and self.amount is None:
            raise ValueError("fixed line requires 'amount'")
        if self.type == LineType.PERCENT and self.percent is None:
            raise ValueError("percent line requires 'percent'")
        # BALANCE needs neither amount nor percent — it's derived from CTC.
        return self


class ResolvedLine(BaseModel):
    code: str
    label: str
    amount: Decimal


# ---------------------------------------------------------------------------
# Salary structures
# ---------------------------------------------------------------------------
class SalaryStructureBase(BaseModel):
    ctc: Decimal = Field(..., description="Annual cost-to-company (0 for hourly staff)")
    currency: str = Field(default="INR", max_length=8)
    pay_frequency: PayFrequency = Field(default=PayFrequency.MONTHLY)
    # Required when pay_frequency is HOURLY: gross = hours_worked * rate.
    hourly_rate: Decimal | None = Field(default=None, ge=0)
    effective_from: date
    components: list[MoneyLine]
    default_deductions: list[MoneyLine] = Field(default_factory=list)
    lop_days: Decimal = Field(
        default=Decimal("0"), ge=0, description="Loss-of-pay days (pro-rates earnings each run)"
    )
    is_active: bool = Field(default=True)
    # ----- Statutory toggles (Phase 1) -----
    pf_enabled: bool = Field(default=False, description="Compute EPF (PF) for this employee")
    pf_cap_at_ceiling: bool = Field(default=True, description="Cap PF wage at the ₹15,000 ceiling")
    pf_wage_codes: list[str] | None = Field(
        default=None, description="Earning codes forming PF wage (defaults to [BASIC])"
    )
    esi_enabled: bool = Field(default=False, description="Compute ESI when within wage limit")
    pt_enabled: bool = Field(default=False, description="Compute Professional Tax by employee state")
    tds_enabled: bool = Field(default=False, description="Estimate & deduct monthly income tax (TDS)")


class SalaryStructureCreate(SalaryStructureBase):
    employee_id: uuid.UUID


class SalaryStructureUpdate(BaseModel):
    ctc: Decimal | None = None
    currency: str | None = Field(default=None, max_length=8)
    pay_frequency: PayFrequency | None = None
    hourly_rate: Decimal | None = Field(default=None, ge=0)
    effective_from: date | None = None
    components: list[MoneyLine] | None = None
    default_deductions: list[MoneyLine] | None = None
    lop_days: Decimal | None = Field(default=None, ge=0)
    is_active: bool | None = None
    pf_enabled: bool | None = None
    pf_cap_at_ceiling: bool | None = None
    pf_wage_codes: list[str] | None = None
    esi_enabled: bool | None = None
    pt_enabled: bool | None = None
    tds_enabled: bool | None = None


class SalaryStructureOut(SalaryStructureBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    employee_id: uuid.UUID
    template_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


# ---------------------------------------------------------------------------
# Structure preview (live estimate — runs the real engine, persists nothing)
# ---------------------------------------------------------------------------
class StructurePreviewIn(BaseModel):
    """A structure draft to compute without saving. Mirrors the statutory
    toggles on the structure form; `employee_id` (optional) lets the preview
    pull the employee's state (PT) and IT declaration (TDS)."""

    employee_id: uuid.UUID | None = None
    ctc: Decimal = Field(
        default=Decimal("0"), ge=0, description="Annual CTC (drives %-of-CTC and balance lines)"
    )
    pay_frequency: PayFrequency = Field(default=PayFrequency.MONTHLY)
    components: list[MoneyLine] = Field(default_factory=list)
    default_deductions: list[MoneyLine] = Field(default_factory=list)
    lop_days: Decimal = Field(default=Decimal("0"), ge=0)
    currency: str = Field(default="INR", max_length=8)
    pf_enabled: bool = False
    pf_cap_at_ceiling: bool = True
    pf_wage_codes: list[str] | None = None
    esi_enabled: bool = False
    pt_enabled: bool = False
    tds_enabled: bool = False


class StructurePreviewOut(BaseModel):
    gross_earnings: Decimal
    total_deductions: Decimal
    net_pay: Decimal
    earnings: list[ResolvedLine] = Field(default_factory=list)
    deductions: list[ResolvedLine] = Field(default_factory=list)
    employer_contributions: list[ResolvedLine] = Field(default_factory=list)
    employer_total: Decimal = Decimal("0.00")
    statutory: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Salary templates (reusable, CTC-driven; apply -> per-employee structures)
# ---------------------------------------------------------------------------
class SkippedEmployee(BaseModel):
    employee_id: uuid.UUID
    reason: str


class SalaryTemplateBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    currency: str = Field(default="INR", max_length=8)
    pay_frequency: PayFrequency = Field(default=PayFrequency.MONTHLY)
    components: list[MoneyLine]
    default_deductions: list[MoneyLine] = Field(default_factory=list)
    pf_enabled: bool = False
    pf_cap_at_ceiling: bool = True
    pf_wage_codes: list[str] | None = None
    esi_enabled: bool = False
    pt_enabled: bool = False
    tds_enabled: bool = False


class SalaryTemplateCreate(SalaryTemplateBase):
    pass


class SalaryTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    currency: str | None = Field(default=None, max_length=8)
    pay_frequency: PayFrequency | None = None
    components: list[MoneyLine] | None = None
    default_deductions: list[MoneyLine] | None = None
    pf_enabled: bool | None = None
    pf_cap_at_ceiling: bool | None = None
    pf_wage_codes: list[str] | None = None
    esi_enabled: bool | None = None
    pt_enabled: bool | None = None
    tds_enabled: bool | None = None


class SalaryTemplateOut(SalaryTemplateBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class TemplateAssignment(BaseModel):
    """One employee to receive a structure generated from the template."""

    employee_id: uuid.UUID
    ctc: Decimal = Field(..., gt=0, description="Annual CTC for this employee")
    effective_from: date


class TemplateApplyIn(BaseModel):
    assignments: list[TemplateAssignment] = Field(..., min_length=1)
    # When an employee already has an active structure: replace it (deactivate
    # the old one) or skip them. Default replace — re-applying a template is the
    # common 'roll out an updated package' action.
    replace_existing: bool = True


class TemplateApplyResult(BaseModel):
    created: list[uuid.UUID] = Field(default_factory=list)  # new structure ids
    skipped: list[SkippedEmployee] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Payroll cycles
# ---------------------------------------------------------------------------
class PayrollCycleBase(BaseModel):
    name: str = Field(..., max_length=120)
    period_start: date
    period_end: date
    pay_date: date
    notes: str | None = None


class PayrollCycleCreate(PayrollCycleBase):
    @model_validator(mode="after")
    def _check_dates(self) -> "PayrollCycleCreate":
        if self.period_end < self.period_start:
            raise ValueError("period_end must be on or after period_start")
        if self.pay_date < self.period_start:
            raise ValueError("pay_date must be on or after period_start")
        return self


class CycleTotals(BaseModel):
    headcount: int = 0
    gross: Decimal = Decimal("0.00")
    deductions: Decimal = Decimal("0.00")
    net: Decimal = Decimal("0.00")


class PayrollCycleOut(PayrollCycleBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    status: PayrollCycleStatus
    totals: dict | None = None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


# ---------------------------------------------------------------------------
# Payslips
# ---------------------------------------------------------------------------
class PayslipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    cycle_id: uuid.UUID
    employee_id: uuid.UUID
    gross_earnings: Decimal
    total_deductions: Decimal
    net_pay: Decimal
    lop_days: Decimal
    paid_days: Decimal | None = None
    currency: str
    status: PayslipStatus
    paid_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class PayslipDetailOut(PayslipOut):
    earnings: list[ResolvedLine]
    deductions: list[ResolvedLine]
    employer_contributions: list[ResolvedLine] = Field(default_factory=list)
    statutory: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Per-run adjustments (one-time earnings/deductions for a cycle)
# ---------------------------------------------------------------------------
class AdjustmentCreate(BaseModel):
    employee_id: uuid.UUID
    kind: AdjustmentKind
    code: str = Field(..., min_length=1, max_length=64)
    label: str = Field(..., min_length=1, max_length=120)
    amount: Decimal = Field(..., gt=0, description="Absolute amount (sign comes from kind)")
    note: str | None = Field(default=None, max_length=500)


class AdjustmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    cycle_id: uuid.UUID
    employee_id: uuid.UUID
    kind: AdjustmentKind
    code: str
    label: str
    amount: Decimal
    note: str | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Run result
# ---------------------------------------------------------------------------
class RunResult(BaseModel):
    created: int
    updated: int
    skipped: list[SkippedEmployee]


# ---------------------------------------------------------------------------
# Dashboard summary (application-wide overview)
# ---------------------------------------------------------------------------
class EmployeeStats(BaseModel):
    total: int = 0
    configured: int = 0  # have an active salary structure
    missing: int = 0  # active employees without a structure


class CycleStats(BaseModel):
    total: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)


class PayrollStats(BaseModel):
    gross_paid: Decimal = Decimal("0.00")  # gross across PAID cycles
    net_paid: Decimal = Decimal("0.00")  # net disbursed across PAID cycles
    payslips_paid: int = 0  # headcount across PAID cycles
    pending_net: Decimal = Decimal("0.00")  # net in PROCESSING/APPROVED cycles


class DashboardCycle(BaseModel):
    id: uuid.UUID
    name: str
    status: PayrollCycleStatus
    period_start: date
    period_end: date
    pay_date: date
    net: Decimal = Decimal("0.00")
    headcount: int = 0


class DashboardSummary(BaseModel):
    employees: EmployeeStats
    active_structures: int = 0
    cycles: CycleStats
    payroll: PayrollStats
    current_cycle: DashboardCycle | None = None
    recent_cycles: list[DashboardCycle] = Field(default_factory=list)
    currency: str = "INR"


# ---------------------------------------------------------------------------
# Payslip email results
# ---------------------------------------------------------------------------
class EmailResult(BaseModel):
    sent: bool
    to: str


class EmailFailure(BaseModel):
    payslip_id: uuid.UUID
    employee_id: uuid.UUID
    reason: str


class BulkEmailResult(BaseModel):
    sent: int = 0
    failed: list[EmailFailure] = Field(default_factory=list)


class MyPayslipOut(PayslipDetailOut):
    """A payslip with its cycle context, for the employee self-service view."""

    cycle_name: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    pay_date: date | None = None
