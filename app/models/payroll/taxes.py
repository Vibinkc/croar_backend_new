import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.enterprise.base import EnterpriseBase as Base
from app.models.payroll._mixin import TimestampMixin
from app.payroll.constants import DEFAULT_FINANCIAL_YEAR, TaxRegime

_MONEY = Numeric(12, 2)
_ZERO = text("0")


class EmployeeTaxProfile(Base, TimestampMixin):
    """An employee's income-tax declaration for a financial year (Zoho "IT
    Declaration"): chosen regime + declared investments/exemptions + previous
    employer income. Captured by HR/employee now; consumed by a future TDS
    engine (not yet built). One profile per employee."""

    __tablename__ = "employee_tax_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), index=True
    )
    financial_year: Mapped[str] = mapped_column(
        String(9), default=DEFAULT_FINANCIAL_YEAR, server_default=DEFAULT_FINANCIAL_YEAR
    )
    tax_regime: Mapped[str] = mapped_column(
        String(8), default=TaxRegime.NEW.value, server_default=TaxRegime.NEW.value
    )
    # Declared deductions / exemptions (annual amounts).
    declared_80c: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"), server_default=_ZERO)
    declared_80d: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"), server_default=_ZERO)
    declared_hra_rent: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"), server_default=_ZERO)
    declared_home_loan_interest: Mapped[Decimal] = mapped_column(
        _MONEY, default=Decimal("0"), server_default=_ZERO
    )
    declared_other: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"), server_default=_ZERO)
    # Income / TDS already deducted by a previous employer this FY.
    prev_employer_income: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"), server_default=_ZERO)
    prev_employer_tds: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"), server_default=_ZERO)

    __table_args__ = (UniqueConstraint("employee_id", name="uq_tax_profile_employee"),)


class TdsChallan(Base, TimestampMixin):
    """A recorded TDS payment to the government (Zoho "Challans"). Manual entry —
    the system stores the challan details for reconciliation/returns; it does not
    pay or file. Self-contained record-keeping."""

    __tablename__ = "tds_challans"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    financial_year: Mapped[str] = mapped_column(
        String(9), default=DEFAULT_FINANCIAL_YEAR, server_default=DEFAULT_FINANCIAL_YEAR
    )
    # "YYYY-MM" month the TDS relates to (the liability period).
    period_month: Mapped[str] = mapped_column(String(7))
    amount: Mapped[Decimal] = mapped_column(_MONEY)
    challan_number: Mapped[str] = mapped_column(String(64))
    bsr_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    deposit_date: Mapped[date] = mapped_column(Date)
    interest: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"), server_default=_ZERO)
    penalty: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"), server_default=_ZERO)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
