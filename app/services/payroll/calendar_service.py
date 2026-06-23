"""Work-calendar helpers: holiday CRUD + deriving the working-day count for a
payroll period from the company calendar (weekly-offs + holidays).

The working-day count is the proration denominator the payroll engine uses
(``working_days`` in compute_payslip). Historically this was a fixed 30; when a
company enables ``use_calendar_working_days`` it becomes the number of scheduled
working days in the period (calendar days minus weekly-offs minus holidays).
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enterprise.company import Company
from app.models.payroll.calendar import Holiday
from app.payroll.constants import DEFAULT_WORKING_DAYS, WEEKDAY_CODES
from app.schemas.enterprise.payroll.settings import WorkCalendarConfig


async def load_calendar_config(db: AsyncSession, company_id: uuid.UUID) -> WorkCalendarConfig:
    """The company's work-calendar config (defaults when unset)."""
    raw = (
        await db.execute(select(Company.statutory_settings).where(Company.id == company_id))
    ).scalar_one_or_none()
    return WorkCalendarConfig.from_stored(raw)


async def get_holiday_dates(db: AsyncSession, company_id: uuid.UUID, start: date, end: date) -> set[date]:
    """Public alias of the holiday-date lookup (used by timesheet seeding)."""
    return await _holiday_dates(db, company_id, start, end)


async def _holiday_dates(db: AsyncSession, company_id: uuid.UUID, start: date, end: date) -> set[date]:
    rows = (
        (
            await db.execute(
                select(Holiday.holiday_date).where(
                    Holiday.company_id == company_id,
                    Holiday.deleted_at.is_(None),
                    Holiday.holiday_date >= start,
                    Holiday.holiday_date <= end,
                )
            )
        )
        .scalars()
        .all()
    )
    return set(rows)


def is_working_day(day: date, weekly_offs: set[str], holidays: set[date]) -> bool:
    """True when `day` is a scheduled working day (not a weekly-off or holiday)."""
    if WEEKDAY_CODES[day.weekday()] in weekly_offs:
        return False
    return day not in holidays


async def working_days_in_period(db: AsyncSession, company_id: uuid.UUID, start: date, end: date) -> Decimal:
    """Working-day count for [start, end] (inclusive).

    Returns the fixed DEFAULT_WORKING_DAYS when the company has calendar-derived
    working days disabled, so existing behaviour is preserved. Otherwise counts
    calendar days that are neither a weekly-off nor a holiday (never below 1, so
    proration never divides by zero on an all-holiday period).
    """
    cfg = await load_calendar_config(db, company_id)
    if not cfg.use_calendar_working_days:
        return Decimal(DEFAULT_WORKING_DAYS)

    weekly_offs = set(cfg.weekly_offs)
    holidays = await _holiday_dates(db, company_id, start, end)
    count = 0
    day = start
    while day <= end:
        if is_working_day(day, weekly_offs, holidays):
            count += 1
        day += timedelta(days=1)
    return Decimal(max(count, 1))


# ---------------------------------------------------------------------------
# Holiday CRUD
# ---------------------------------------------------------------------------
async def list_holidays(db: AsyncSession, company_id: uuid.UUID) -> list[Holiday]:
    rows = (
        (
            await db.execute(
                select(Holiday)
                .where(Holiday.company_id == company_id, Holiday.deleted_at.is_(None))
                .order_by(Holiday.holiday_date)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def create_holiday(db: AsyncSession, company_id: uuid.UUID, holiday_date: date, name: str) -> Holiday:
    holiday = Holiday(company_id=company_id, holiday_date=holiday_date, name=name)
    db.add(holiday)
    try:
        await db.commit()
        await db.refresh(holiday)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="A holiday already exists on this date."
        )
    except Exception:
        await db.rollback()
        raise
    return holiday


async def delete_holiday(db: AsyncSession, company_id: uuid.UUID, holiday_id: uuid.UUID) -> None:
    holiday = (
        await db.execute(
            select(Holiday).where(
                Holiday.id == holiday_id, Holiday.company_id == company_id, Holiday.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if not holiday:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Holiday not found.")
    holiday.deleted_at = datetime.now(UTC).replace(tzinfo=None)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise
