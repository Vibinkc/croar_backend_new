"""Attendance — presence, which is not the same thing as a timesheet.

Croar already had timesheets: hours claimed against work, per period, feeding payroll. It had no
record of whether anyone actually turned up. Payroll has been inferring that from the timesheet,
which is the sort of quiet approximation nobody notices until somebody disputes a payslip.

Oorwin's Attendance screen was read from their live product and its columns are the model here:
Date, Shift, Status, Attendance Source, In Time, In Log, Out Time, Out Log, Work Hours, Work
Mode — with tabs for Leaves and Regularization.

Four tables, because "In Time" and "In Log" are two different things:

  * `shifts` — when a working day is supposed to start and end.
  * `attendance_days` — one row per person per date. The summary: shift, status, first in, last
    out, minutes worked, how it was recorded.
  * `attendance_punches` — every individual in/out event. This is the "log" column, and it is
    separate because a day can have four punches (in, lunch out, lunch in, out) while still
    having exactly one first-in and one last-out.
  * `attendance_regularizations` — the request an employee raises when the log is wrong.

Regularization exists because punches get missed, and the alternative to a request-and-approve
path is letting people edit their own attendance, which makes the record worthless.
"""

import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    Date,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text

from .base import EnterpriseBase

# present/absent are the two that matter for pay. half_day is its own status rather than a
# fraction, because payroll already counts half_days separately. leave, holiday and weekly_off
# are all "not at work and not being docked", but they are different reasons and reporting on
# absence has to be able to tell them apart.
ATTENDANCE_STATUSES = ("present", "absent", "half_day", "leave", "holiday", "weekly_off")

# How the row came to exist. Kept because an audit of "who was marked present by hand" is the
# first question anyone asks when the numbers look wrong.
ATTENDANCE_SOURCES = ("web", "biometric", "mobile", "manual", "import", "regularization")

WORK_MODES = ("office", "remote", "hybrid", "field")

PUNCH_DIRECTIONS = ("in", "out")

REGULARIZATION_STATUSES = ("pending", "approved", "rejected", "cancelled")


class Shift(EnterpriseBase):
    """When a working day is supposed to start and end."""

    __tablename__ = "shifts"
    __table_args__ = (UniqueConstraint("company_id", "name", name="uq_shifts_company_name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    starts_at: Mapped[time] = mapped_column(Time, nullable=False)
    ends_at: Mapped[time] = mapped_column(Time, nullable=False)
    # Unpaid break, subtracted from the gap between first in and last out.
    break_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="60")
    # Below this, the day is not a full day. Stored per shift because a 6-hour shift and a
    # 9-hour one cannot share a threshold.
    half_day_after_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="240")
    full_day_after_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="480")
    # Monday=0 … Sunday=6, as a string of digits. A list column would be tidier but this is read
    # on every day-generation and a seven-character string needs no join and no array support.
    working_days: Mapped[str] = mapped_column(String(7), nullable=False, server_default="01234")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)


class AttendanceDay(EnterpriseBase):
    """One person, one date. The summary the rest of the product reads."""

    __tablename__ = "attendance_days"
    __table_args__ = (
        # One row per person per day. Without this a second punch-in on a different device
        # creates a duplicate day and every total silently doubles.
        UniqueConstraint("employee_id", "work_date", name="uq_attendance_employee_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    work_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    shift_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("shifts.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="absent", index=True)
    work_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, server_default="web")

    # Derived from the punches, stored so a month view is one query rather than one per day.
    first_in: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)
    last_out: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)
    work_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    late_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set once the period has been pushed to a timesheet. A locked day cannot be punched or
    # regularized, because changing it after payroll has read it makes the payslip a lie.
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )

    punches = relationship(
        "AttendancePunch",
        back_populates="day",
        order_by="AttendancePunch.punched_at",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    employee = relationship("Employee", lazy="selectin")


class AttendancePunch(EnterpriseBase):
    """One in or out event. The raw log behind the day's summary."""

    __tablename__ = "attendance_punches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    day_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("attendance_days.id", ondelete="CASCADE"), nullable=False, index=True
    )
    direction: Mapped[str] = mapped_column(String(4), nullable=False)
    punched_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, server_default="web")
    # Free text rather than coordinates: this is "Bangalore office" or "client site", which is
    # what anyone reviewing a log actually wants, and it avoids storing precise location.
    location: Mapped[str | None] = mapped_column(String(120), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())

    day = relationship("AttendanceDay", back_populates="punches")


class AttendanceRegularization(EnterpriseBase):
    """A request to correct a day, because punches get missed.

    The alternative is letting people edit their own attendance directly, which makes the record
    worth nothing. So the correction is a request with a reason, and somebody else approves it —
    and the approval is what writes the change.
    """

    __tablename__ = "attendance_regularizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    work_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    # What they are asking for. Applied to the day only once approved.
    requested_status: Mapped[str] = mapped_column(String(20), nullable=False)
    requested_in: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)
    requested_out: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)
    requested_work_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending", index=True)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False, server_default=func.now())
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )

    employee = relationship("Employee", lazy="selectin")
