from pydantic import BaseModel, EmailStr
from uuid import UUID
from datetime import datetime
from typing import Optional

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
