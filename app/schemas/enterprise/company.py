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
    logo_url: str | None = None
    config: dict[str, Any] | None = None
    is_consultancy: bool | None = None
    parent_id: UUID | None = None


class CompanyResponse(CompanyBase):
    id: UUID
    slug: str
    is_consultancy: bool
    parent_id: UUID | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
