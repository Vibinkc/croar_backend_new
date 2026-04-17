from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr


class StudentBase(BaseModel):
    full_name: str
    email: EmailStr


class StudentCreate(StudentBase):
    password: str


class StudentResponse(StudentBase):
    id: UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class StudentLogin(BaseModel):
    email: EmailStr
    password: str
