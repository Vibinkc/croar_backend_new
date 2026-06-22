"""Leave management: leave types, per-employee balances, and leave requests.

This is the balance ledger behind the timesheet's PAID_LEAVE / UNPAID_LEAVE
day statuses. A company defines `LeaveType`s (paid/unpaid, with an annual quota
and accrual method); each employee gets a `LeaveBalance` per type per financial
year; a `LeaveRequest` is filed for a date range and, when APPROVED, decrements
the balance and stamps the covered timesheet days (see leave_service +
timesheet_service.resync_leave).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.enterprise.base import EnterpriseBase as Base
from app.models.payroll._mixin import TimestampMixin
from app.payroll.constants import DEFAULT_FINANCIAL_YEAR, AccrualMethod, LeaveStatus


class LeaveType(Base, TimestampMixin):
    """A category of leave a company offers (e.g. Casual, Sick, Earned, LOP).

    `is_paid` decides whether approved days land as PAID_LEAVE (no LOP) or
    UNPAID_LEAVE (full LOP) on the timesheet. Paid types carry an `annual_quota`
    that accrues into each employee's balance per the `accrual` method.
    """

    __tablename__ = "leave_types"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(80))
    code: Mapped[str] = mapped_column(String(16))
    is_paid: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    annual_quota: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), default=Decimal("0"), server_default=text("0")
    )
    accrual: Mapped[str] = mapped_column(
        String(16), default=AccrualMethod.ANNUAL.value, server_default=AccrualMethod.ANNUAL.value
    )
    # Max days that may carry into the next FY (informational for now; reserved
    # for a future carry-forward job). Null = no cap.
    carry_forward_cap: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))

    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_leave_type_company_code"),)


class LeaveBalance(Base, TimestampMixin):
    """An employee's running balance for one leave type in one financial year.

    balance = accrued - used. `entitled` is the full annual quota (for display);
    `accrued` is how much has been credited so far (== entitled for ANNUAL types,
    or pro-rated by month for MONTHLY types).
    """

    __tablename__ = "leave_balances"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), index=True
    )
    leave_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leave_types.id", ondelete="CASCADE"), index=True
    )
    financial_year: Mapped[str] = mapped_column(
        String(7), default=DEFAULT_FINANCIAL_YEAR, server_default=DEFAULT_FINANCIAL_YEAR
    )
    entitled: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0"), server_default=text("0"))
    accrued: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0"), server_default=text("0"))
    used: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0"), server_default=text("0"))

    __table_args__ = (
        UniqueConstraint(
            "employee_id", "leave_type_id", "financial_year", name="uq_leave_balance_emp_type_fy"
        ),
    )


class LeaveRequest(Base, TimestampMixin):
    """A leave application for a date range. APPROVED requests decrement the
    matching balance and stamp the covered timesheet days."""

    __tablename__ = "leave_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), index=True
    )
    leave_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leave_types.id"), index=True
    )
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    # Number of leave days requested (supports 0.5 for a half-day).
    days: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    # True when the range is a single half-day.
    half_day: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    status: Mapped[str] = mapped_column(
        String(16), default=LeaveStatus.PENDING.value, server_default=LeaveStatus.PENDING.value
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)
