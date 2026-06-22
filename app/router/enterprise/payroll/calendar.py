import uuid

from fastapi import APIRouter, Depends, status
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
