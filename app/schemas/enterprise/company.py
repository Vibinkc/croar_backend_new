from typing import Optional
from pydantic import BaseModel
from datetime import datetime
from uuid import UUID

class CompanyBase(BaseModel):
    name: str
    industry: Optional[str] = None
    location: Optional[str] = None
    logo_url: Optional[str] = None
    config: Optional[dict] = {}

class CompanyCreate(CompanyBase):
    pass

class CompanyUpdate(BaseModel):
    name: Optional[str] = None
    industry: Optional[str] = None
    location: Optional[str] = None
    logo_url: Optional[str] = None
    config: Optional[dict] = None

class CompanyResponse(CompanyBase):
    id: UUID
    slug: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
