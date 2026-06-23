import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.payroll.constants import AccrualMethod


# ---------------------------------------------------------------------------
# Leave types
# ---------------------------------------------------------------------------
class LeaveTypeIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    code: str = Field(min_length=1, max_length=16)
    is_paid: bool = True
    annual_quota: Decimal = Field(default=Decimal("0"), ge=0)
    accrual: AccrualMethod = AccrualMethod.ANNUAL
    carry_forward_cap: Decimal | None = Field(default=None, ge=0)
    is_active: bool = True

    @field_validator("code")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


class LeaveTypeUpdate(BaseModel):
    """Partial update — only provided fields change."""

    name: str | None = Field(default=None, min_length=1, max_length=80)
    is_paid: bool | None = None
    annual_quota: Decimal | None = Field(default=None, ge=0)
    accrual: AccrualMethod | None = None
    carry_forward_cap: Decimal | None = Field(default=None, ge=0)
    is_active: bool | None = None


class LeaveTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    code: str
    is_paid: bool
    annual_quota: Decimal
    accrual: str
    carry_forward_cap: Decimal | None = None
    is_active: bool


# ---------------------------------------------------------------------------
# Balances
# ---------------------------------------------------------------------------
class LeaveBalanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_id: uuid.UUID
    leave_type_id: uuid.UUID
    financial_year: str
    entitled: Decimal
    accrued: Decimal
    used: Decimal
    # Derived (accrued - used); filled by the service/router.
    balance: Decimal = Decimal("0")
    # Display helpers.
    employee_name: str | None = None
    leave_type_name: str | None = None
    leave_type_code: str | None = None
    is_paid: bool | None = None


# ---------------------------------------------------------------------------
# Leave requests
# ---------------------------------------------------------------------------
class LeaveRequestIn(BaseModel):
    employee_id: uuid.UUID
    leave_type_id: uuid.UUID
    start_date: date
    end_date: date
    half_day: bool = False
    reason: str | None = Field(default=None, max_length=500)


class MyLeaveRequestIn(BaseModel):
    """Self-service leave application. No employee_id — the server fills it from
    the caller's linked employee so a user can only file leave for themselves."""

    leave_type_id: uuid.UUID
    start_date: date
    end_date: date
    half_day: bool = False
    reason: str | None = Field(default=None, max_length=500)


class LeaveDecisionIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class LeaveRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_id: uuid.UUID
    leave_type_id: uuid.UUID
    start_date: date
    end_date: date
    days: Decimal
    half_day: bool
    status: str
    reason: str | None = None
    decision_note: str | None = None
    decided_at: datetime | None = None
    # Display helpers (resolved in the router).
    employee_name: str | None = None
    leave_type_name: str | None = None
    leave_type_code: str | None = None
