import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.enterprise.base import EnterpriseBase as Base
from app.models.enterprise.employee import Employee
from app.models.payroll._mixin import TimestampMixin
from app.payroll.constants import PayrollCycleStatus, PayslipStatus


class SalaryStructure(Base, TimestampMixin):
    """One active salary package per employee (spec §4.1)."""

    __tablename__ = "salary_structures"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("employees.id"), index=True)
    ctc: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    pay_frequency: Mapped[str] = mapped_column(String(16), default="MONTHLY")
    # Hourly-paid staff (pay_frequency == HOURLY): gross = hours_worked * rate.
    # Null for salaried (MONTHLY/WEEKLY) structures. See compute_hourly_payslip.
    hourly_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    effective_from: Mapped[date] = mapped_column(Date)
    # Earning lines: [{code,label,type,amount|percent,percent_of}]
    components: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    # Recurring deduction lines applied each run (same union as components)
    default_deductions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    # Loss-of-pay days HR enters per employee; pro-rates earnings each run.
    lop_days: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"), server_default=text("0"))
    is_active: Mapped[bool] = mapped_column(default=True)

    # ----- Statutory toggles (Phase 1) -----
    # Off by default so existing structures compute exactly as before until HR
    # opts in. `pf_wage_codes` lists which earning codes form the PF wage base
    # (defaults to ["BASIC"] when null). PT applicability follows the employee's
    # state when `pt_enabled`.
    pf_enabled: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    pf_cap_at_ceiling: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    pf_wage_codes: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True, default=None)
    esi_enabled: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    pt_enabled: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    # Income tax (TDS) — Phase 2. Opt-in; when on, an estimated monthly TDS is
    # computed from the employee's IT declaration and deducted on the payslip.
    tds_enabled: Mapped[bool] = mapped_column(default=False, server_default=text("false"))

    # Traceability: the template this structure was generated from, if any.
    # Nullable — structures can still be authored directly. ON DELETE SET NULL so
    # deleting a template never cascades into employee pay.
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("salary_templates.id", ondelete="SET NULL"), nullable=True, index=True
    )

    employee: Mapped["Employee"] = relationship(back_populates="salary_structures")

    __table_args__ = (
        # Only one active, non-deleted structure per employee.
        Index(
            "idx_active_salary_structure",
            "employee_id",
            unique=True,
            postgresql_where=text("is_active AND deleted_at IS NULL"),
        ),
    )


class SalaryTemplate(Base, TimestampMixin):
    """A reusable, CTC-driven salary template (like Zoho/RazorpayX "Salary
    Templates"). Unlike a SalaryStructure it carries no employee or CTC — its
    component lines are *rules* (percent-of-CTC + a balancing line), so applying
    it to any employee scales the amounts to that employee's CTC. Applying a
    template snapshots its lines into a per-employee SalaryStructure (see
    payroll_service.apply_template); the structure keeps `template_id` for
    traceability, but is otherwise independent thereafter."""

    __tablename__ = "salary_templates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    pay_frequency: Mapped[str] = mapped_column(String(16), default="MONTHLY")
    # Same JSON shape as SalaryStructure; lines may use percent_of="CTC" and a
    # type="balance" line so the package stays CTC-driven.
    components: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    default_deductions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)

    # ----- Statutory toggles (copied onto structures at apply time) -----
    pf_enabled: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    pf_cap_at_ceiling: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    pf_wage_codes: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True, default=None)
    esi_enabled: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    pt_enabled: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    tds_enabled: Mapped[bool] = mapped_column(default=False, server_default=text("false"))

    __table_args__ = (
        # Template names are unique per company (amongst non-deleted rows).
        Index(
            "idx_unique_template_name",
            "company_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class PayrollCycle(Base, TimestampMixin):
    """A pay period (spec §4.2)."""

    __tablename__ = "payroll_cycles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    pay_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(
        String(16), default=PayrollCycleStatus.DRAFT.value, server_default=PayrollCycleStatus.DRAFT.value
    )
    # Roll-up: { headcount, gross, deductions, net }
    totals: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    payslips: Mapped[list["Payslip"]] = relationship(back_populates="cycle", cascade="all, delete-orphan")


class PayrollAdjustment(Base, TimestampMixin):
    """A one-time earning or deduction attached to a specific cycle + employee
    (per-run override). Unlike salary-structure lines these do not recur: they
    apply only to the cycle they belong to. Picked up on the next ``run``.

    Deleting the cycle cascades these away. Editable only while the cycle is
    DRAFT/PROCESSING (enforced in the router)."""

    __tablename__ = "payroll_adjustments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    cycle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payroll_cycles.id", ondelete="CASCADE"), index=True
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("employees.id"), index=True)
    # "earning" | "deduction" (constants.AdjustmentKind)
    kind: Mapped[str] = mapped_column(String(16))
    code: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(120))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("idx_adjustment_cycle_employee", "cycle_id", "employee_id"),)


class Payslip(Base, TimestampMixin):
    """One row per employee per cycle (spec §4.3). Earnings/deductions are
    snapshotted at run time, so later structure edits never mutate history."""

    __tablename__ = "payslips"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    cycle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payroll_cycles.id", ondelete="CASCADE"), index=True
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("employees.id"), index=True)
    gross_earnings: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    total_deductions: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    net_pay: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    lop_days: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0.0"))
    paid_days: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    # Resolved line items [{code,label,amount}] (snapshot)
    earnings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    deductions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    # Employer-side statutory cost [{code,label,amount}] — not deducted from the
    # employee; informational/CTC (e.g. employer PF, ESI). Snapshot at run time.
    employer_contributions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=True, default=list)
    # Statutory computation snapshot: ruleset version + per-head breakdown, so a
    # re-rendered payslip reflects the rules that applied when it was run.
    statutory: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True, default=None)
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    status: Mapped[str] = mapped_column(
        String(16), default=PayslipStatus.PENDING.value, server_default=PayslipStatus.PENDING.value
    )
    paid_at: Mapped[datetime | None] = mapped_column(nullable=True)

    cycle: Mapped[PayrollCycle] = relationship(back_populates="payslips")
    employee: Mapped["Employee"] = relationship()

    __table_args__ = (UniqueConstraint("cycle_id", "employee_id", name="uq_payslip_cycle_employee"),)
