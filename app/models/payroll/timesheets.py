import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.enterprise.base import EnterpriseBase as Base
from app.models.payroll._mixin import TimestampMixin
from app.payroll.constants import TimesheetMode, TimesheetStatus


class Timesheet(Base, TimestampMixin):
    """One timesheet per employee per payroll cycle (the header).

    Daily detail lives in TimesheetEntry; the aggregate columns here
    (worked_days / lop_days / half_days / total_hours) are recomputed from those
    entries on every edit (see timesheet_service.recompute_aggregates) so a
    payroll run can read them directly. Only APPROVED timesheets feed a run.
    """

    __tablename__ = "timesheets"

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
    # Copied from the cycle at generate time (the days the entries span).
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    # ATTENDANCE | HOURLY (constants.TimesheetMode), derived from the employee's
    # active structure pay_frequency when the timesheet is generated.
    mode: Mapped[str] = mapped_column(
        String(16), default=TimesheetMode.ATTENDANCE.value, server_default=TimesheetMode.ATTENDANCE.value
    )
    status: Mapped[str] = mapped_column(
        String(16), default=TimesheetStatus.DRAFT.value, server_default=TimesheetStatus.DRAFT.value
    )

    # ----- Cached aggregates (recomputed from entries) -----
    worked_days: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), default=Decimal("0"), server_default=text("0")
    )
    lop_days: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0"), server_default=text("0"))
    half_days: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0"), server_default=text("0"))
    total_hours: Mapped[Decimal] = mapped_column(
        Numeric(8, 2), default=Decimal("0"), server_default=text("0")
    )

    submitted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # Who submitted / approved (segregation-of-duties / maker-checker). Nullable
    # FKs to users; the approve guard can reject same-actor approval when the
    # company has enforce_maker_checker on. ondelete=SET NULL so deleting a user
    # never cascades away a timesheet.
    submitted_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    entries: Mapped[list["TimesheetEntry"]] = relationship(
        back_populates="timesheet", cascade="all, delete-orphan", order_by="TimesheetEntry.entry_date"
    )

    __table_args__ = (UniqueConstraint("cycle_id", "employee_id", name="uq_timesheet_cycle_employee"),)


class TimesheetEntry(Base, TimestampMixin):
    """One day of a timesheet. ATTENDANCE timesheets use `day_status`; HOURLY
    timesheets use `hours`. Unique per (timesheet, date)."""

    __tablename__ = "timesheet_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    timesheet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("timesheets.id", ondelete="CASCADE"), index=True
    )
    entry_date: Mapped[date] = mapped_column(Date)
    # constants.DayStatus — meaningful in ATTENDANCE mode.
    day_status: Mapped[str] = mapped_column(String(16))
    # Hours worked — meaningful in HOURLY mode (null for attendance days).
    hours: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    timesheet: Mapped[Timesheet] = relationship(back_populates="entries")

    __table_args__ = (
        UniqueConstraint("timesheet_id", "entry_date", name="uq_entry_timesheet_date"),
        Index("idx_entry_timesheet", "timesheet_id"),
    )
