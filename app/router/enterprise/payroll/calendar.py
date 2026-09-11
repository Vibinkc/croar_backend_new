import csv
import io
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select

from app.models.enterprise.company import Company
from app.models.payroll.calendar import Holiday
from app.payroll.constants import Permission
from app.payroll.deps import DBSessionDep, get_current_company_id, require_permission
from app.schemas.enterprise.payroll.settings import (
    HolidayIn,
    HolidayOut,
    WorkCalendarConfig,
    WorkCalendarConfigUpdate,
)
from app.services.payroll import calendar_service

router = APIRouter(prefix="/api/v1/enterprise/calendar", tags=["calendar"])


@router.get("/config", response_model=WorkCalendarConfig)
async def get_calendar_config(
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> WorkCalendarConfig:
    """Working-day derivation config (weekly-offs + calendar toggle)."""
    return await calendar_service.load_calendar_config(db, company_id)


@router.put("/config", response_model=WorkCalendarConfig)
async def update_calendar_config(
    payload: WorkCalendarConfigUpdate,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.USERS_MANAGE)),
) -> WorkCalendarConfig:
    """Edit the work-calendar config. Admin-only (users:manage). Stored in the
    company's statutory_settings JSON alongside the statutory rates."""
    company = (
        await db.execute(select(Company).where(Company.id == company_id, Company.deleted_at.is_(None)))
    ).scalar_one()
    changes = payload.model_dump(exclude_unset=True)
    merged = {**(company.statutory_settings or {}), **changes}
    # Re-validate the calendar slice through the canonical model, then write the
    # cleaned keys back over the stored blob (leaving statutory rate keys intact).
    clean = WorkCalendarConfig.from_stored(merged).model_dump()
    company.statutory_settings = {**(company.statutory_settings or {}), **clean}
    try:
        await db.commit()
        await db.refresh(company)
    except Exception:
        await db.rollback()
        raise
    return WorkCalendarConfig.from_stored(company.statutory_settings)


@router.get("/holidays", response_model=list[HolidayOut])
async def list_holidays(
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> list[Holiday]:
    return await calendar_service.list_holidays(db, company_id)


@router.post("/holidays", response_model=HolidayOut, status_code=status.HTTP_201_CREATED)
async def create_holiday(
    payload: HolidayIn,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_CONFIGURE)),
) -> Holiday:
    return await calendar_service.create_holiday(db, company_id, payload.holiday_date, payload.name.strip())


@router.delete("/holidays/{holiday_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_holiday(
    holiday_id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_CONFIGURE)),
) -> None:
    await calendar_service.delete_holiday(db, company_id, holiday_id)


# ---------------------------------------------------------------------------
# Bulk import
#
# A year of public holidays entered one at a time is a job nobody actually does, and an empty
# work calendar silently inflates every month's working days — which under-pays anyone with
# loss-of-pay. Accepting a spreadsheet is what makes the calendar likely to be filled in at all.
# ---------------------------------------------------------------------------
_DATE_FORMATS = (
    "%Y-%m-%d",  # 2026-01-26   ISO, and what Excel exports when the cell is a real date
    "%d-%m-%Y",  # 26-01-2026   the Indian convention
    "%d/%m/%Y",  # 26/01/2026
    "%m/%d/%Y",  # 01/26/2026   US Excel default; last, so ambiguous dates read as day-first
    "%d-%b-%Y",  # 26-Jan-2026
    "%d %B %Y",  # 26 January 2026
)


def _parse_date(raw: str) -> date | None:
    """Read a date the way a spreadsheet is likely to have written it.

    Order matters on the ambiguous ones. 01/02/2026 is read as 1 February, not 2 January,
    because the day-first formats are tried first — this is an Indian payroll product and
    guessing US order would silently move a holiday.
    """
    text = raw.strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    # Excel sometimes hands over a serial number instead of a formatted date.
    try:
        serial = int(float(text))
    except ValueError:
        return None
    if 1 <= serial <= 200000:
        # Excel's epoch is 1899-12-30: it counts 1900 as a leap year, which it was not.
        return date(1899, 12, 30) + timedelta(days=serial)
    return None


@router.post("/holidays/import")
async def import_holidays(
    db: DBSessionDep,
    file: UploadFile = File(...),
    replace: bool = Form(default=False),
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_CONFIGURE)),
) -> dict[str, Any]:
    """Import holidays from a CSV or Excel file.

    Two columns are needed, in any order, named anything reasonable: a date and a name. The
    header is matched loosely because a file exported from anywhere says "Date"/"Holiday"/
    "Occasion" rather than the exact keys this API would prefer.

    Rows that cannot be read are reported rather than silently dropped, so a file with three
    bad dates imports the rest and tells you which three to fix.
    """
    raw = await file.read()
    if len(raw) > 2 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="That file is larger than 2 MB.")

    filename = (file.filename or "").lower()
    if filename.endswith((".xlsx", ".xls")):
        # A real .xlsx is a zip, and nothing in this project reads one. Rejecting with the
        # remedy is more useful than a parse error, and Excel exports CSV in two clicks.
        raise HTTPException(
            status_code=400, detail="Save the sheet as CSV first (File → Save As → CSV), then upload that."
        )

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Excel on Windows writes the regional codepage unless you ask for UTF-8.
        text = raw.decode("cp1252", errors="replace")

    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows:
        raise HTTPException(status_code=400, detail="That file has no rows in it.")

    # Work out which column is the date and which is the name. If the first row parses as a
    # date it is data, not a header, so a file with no header still imports.
    header = [c.strip().lower() for c in rows[0]]
    date_idx, name_idx = 0, 1
    body = rows
    if _parse_date(rows[0][0] if rows[0] else "") is None:
        # Three tiers, strongest signal first, because the obvious headers overlap:
        # "Holiday" contains "day", and "Holiday Date" contains both "holiday" and "date".
        # Testing either word list first alone gets one of those two cases wrong.
        #   1. the literal word "date" — unambiguous, wins outright
        #   2. a name word
        #   3. a weaker date word, for headers like "Day" or "On"
        name_words = ("holiday", "occasion", "festival", "name", "description", "title", "event")
        weak_date_words = ("day", "on")
        found_date = found_name = None
        for i, col in enumerate(header):
            if "date" in col:
                if found_date is None:
                    found_date = i
            elif any(k in col for k in name_words):
                if found_name is None:
                    found_name = i
            elif any(k in col for k in weak_date_words):
                if found_date is None:
                    found_date = i
        if found_date is not None:
            date_idx = found_date
        if found_name is not None:
            name_idx = found_name
        # A header naming only one of the two leaves the other at its default, which would
        # collide. Fall back to "the other column".
        if date_idx == name_idx:
            name_idx = 1 if date_idx == 0 else 0
        body = rows[1:]

    parsed: list[tuple[date, str]] = []
    invalid: list[dict[str, str]] = []
    for line_no, row in enumerate(body, start=2):
        raw_date = row[date_idx] if date_idx < len(row) else ""
        raw_name = row[name_idx] if name_idx < len(row) else ""
        holiday_date = _parse_date(raw_date)
        name = raw_name.strip()[:160]
        if holiday_date is None:
            invalid.append({"row": str(line_no), "value": raw_date.strip(), "reason": "unreadable date"})
            continue
        if not name:
            invalid.append({"row": str(line_no), "value": raw_date.strip(), "reason": "no name"})
            continue
        parsed.append((holiday_date, name))

    if not parsed:
        raise HTTPException(
            status_code=400, detail="No usable rows. Expected two columns: a date and a holiday name."
        )

    result = await calendar_service.import_holidays(db, company_id, parsed, replace=replace)
    return {**result, "invalid": invalid}
