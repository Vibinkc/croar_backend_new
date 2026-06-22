"""Shared timestamp columns for the payroll-specific tables.

Ported from the payroll module's ``Company.TimestampMixin``. Every payroll table
mixes this in for created/updated/soft-delete columns, matching the convention
used by the rest of the payroll domain.
"""

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    """Audit columns shared by every payroll table."""

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(default=None, nullable=True)
