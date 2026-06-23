import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.payroll.constants import DayStatus


class TimesheetEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entry_date: date
    day_status: str
    hours: Decimal | None = None
    note: str | None = None


class TimesheetEntryIn(BaseModel):
    """One day's edit. `day_status` drives ATTENDANCE timesheets; `hours` drives
    HOURLY ones. Matched to an existing entry by `entry_date`."""

    entry_date: date
    day_status: DayStatus | None = None
    hours: Decimal | None = Field(default=None, ge=0, le=24)
    note: str | None = Field(default=None, max_length=500)


class TimesheetBulkEntryUpdate(BaseModel):
    entries: list[TimesheetEntryIn] = Field(default_factory=list)


class TimesheetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    cycle_id: uuid.UUID
    employee_id: uuid.UUID
    period_start: date
    period_end: date
    mode: str
    status: str
    worked_days: Decimal
    lop_days: Decimal
    half_days: Decimal
    total_hours: Decimal
    submitted_at: datetime | None = None
    approved_at: datetime | None = None
    submitted_by_id: uuid.UUID | None = None
    approved_by_id: uuid.UUID | None = None
    notes: str | None = None


class TimesheetDetailOut(TimesheetOut):
    """Header + the daily grid (used by the per-timesheet editor)."""

    entries: list[TimesheetEntryOut] = Field(default_factory=list)
    # Employee display fields, so the editor can render a name without a 2nd call.
    employee_name: str | None = None
    employee_code: str | None = None
    # Who submitted / approved (segregation-of-duties display).
    submitted_by_name: str | None = None
    approved_by_name: str | None = None


class TimesheetSummaryOut(TimesheetOut):
    """Row in the per-cycle list — adds employee display fields."""

    employee_name: str | None = None
    employee_code: str | None = None


class TimesheetGenerateResult(BaseModel):
    created: int
    existing: int
    skipped: list[dict] = Field(default_factory=list)


class TimesheetRejectIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class AttendanceImportResult(BaseModel):
    """Outcome of a CSV/biometric attendance import: how many daily entries were
    updated, and a per-row list of rows that couldn't be applied (with reasons)."""

    updated: int
    skipped: list[dict] = Field(default_factory=list)
