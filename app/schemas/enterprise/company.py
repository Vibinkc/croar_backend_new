from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class CompanyBase(BaseModel):
    name: str
    industry: str | None = None
    location: str | None = None
    logo_url: str | None = None
    config: dict[str, Any] | None = {}
    is_consultancy: bool | None = False
    parent_id: UUID | None = None


class CompanyCreate(CompanyBase):
    pass


class CompanyUpdate(BaseModel):
    name: str | None = None
    industry: str | None = None
    location: str | None = None
    # Editable so an organisation can actually BE Malaysian/Singaporean/etc. The column
    # existed and defaulted to INR, but nothing could ever change it, so every company was
    # permanently Indian as far as salary and payroll were concerned.
    currency: str | None = None
    country: str | None = None
    logo_url: str | None = None
    config: dict[str, Any] | None = None
    is_consultancy: bool | None = None
    is_active: bool | None = None
    parent_id: UUID | None = None


class CompanyResponse(CompanyBase):
    id: UUID
    slug: str
    is_consultancy: bool
    is_active: bool = True
    parent_id: UUID | None
    # Exposed so the UI can label money in the organisation's own currency instead of
    # assuming INR — the job form used to hardcode "INR" for every company.
    currency: str = "INR"
    country: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
