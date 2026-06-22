import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.enterprise.base import EnterpriseBase as Base
from app.models.payroll._mixin import TimestampMixin


class Holiday(Base, TimestampMixin):
    """A company holiday. Holidays (plus the configured weekly-offs) are excluded
    when deriving the working-day count for a payroll period — see
    calendar_service.working_days_in_period. One row per (company, date)."""

    __tablename__ = "holidays"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    holiday_date: Mapped[date] = mapped_column(Date, index=True)
    name: Mapped[str] = mapped_column(String(160))

    __table_args__ = (
        # One holiday per date per company (amongst non-deleted rows).
        UniqueConstraint("company_id", "holiday_date", name="uq_holiday_company_date"),
    )
