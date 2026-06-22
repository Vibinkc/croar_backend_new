import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.payroll.constants import DEFAULT_FINANCIAL_YEAR, TaxRegime


# ---------------------------------------------------------------------------
# Employee tax profile (IT declaration)
# ---------------------------------------------------------------------------
class TaxProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    employee_id: uuid.UUID
    financial_year: str
    tax_regime: TaxRegime
    declared_80c: Decimal
    declared_80d: Decimal
    declared_hra_rent: Decimal
    declared_home_loan_interest: Decimal
    declared_other: Decimal
    prev_employer_income: Decimal
    prev_employer_tds: Decimal
    created_at: datetime
    updated_at: datetime


class TaxProfileUpsert(BaseModel):
    """Full replace of an employee's declaration (all fields have defaults so a
    partial form still validates)."""

    financial_year: str = Field(default=DEFAULT_FINANCIAL_YEAR, max_length=9)
    tax_regime: TaxRegime = TaxRegime.NEW
    declared_80c: Decimal = Field(default=Decimal("0"), ge=0)
    declared_80d: Decimal = Field(default=Decimal("0"), ge=0)
    declared_hra_rent: Decimal = Field(default=Decimal("0"), ge=0)
    declared_home_loan_interest: Decimal = Field(default=Decimal("0"), ge=0)
    declared_other: Decimal = Field(default=Decimal("0"), ge=0)
    prev_employer_income: Decimal = Field(default=Decimal("0"), ge=0)
    prev_employer_tds: Decimal = Field(default=Decimal("0"), ge=0)


# ---------------------------------------------------------------------------
# TDS challans
# ---------------------------------------------------------------------------
class ChallanCreate(BaseModel):
    financial_year: str = Field(default=DEFAULT_FINANCIAL_YEAR, max_length=9)
    period_month: str = Field(..., pattern=r"^\d{4}-\d{2}$", description="YYYY-MM")
    amount: Decimal = Field(..., gt=0)
    challan_number: str = Field(..., min_length=1, max_length=64)
    bsr_code: str | None = Field(default=None, max_length=16)
    deposit_date: date
    interest: Decimal = Field(default=Decimal("0"), ge=0)
    penalty: Decimal = Field(default=Decimal("0"), ge=0)
    notes: str | None = Field(default=None, max_length=500)


class TdsLiabilityRow(BaseModel):
    """Per-month reconciliation: TDS withheld on payslips vs TDS deposited via
    recorded challans."""

    period_month: str
    tds_deducted: Decimal
    tds_deposited: Decimal
    difference: Decimal  # deducted - deposited (positive = still owed)


class ChallanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    financial_year: str
    period_month: str
    amount: Decimal
    challan_number: str
    bsr_code: str | None = None
    deposit_date: date
    interest: Decimal
    penalty: Decimal
    notes: str | None = None
    created_at: datetime
    updated_at: datetime
