import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.payroll.constants import Role


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    email: EmailStr
    full_name: str
    role: str
    is_active: bool
    # Resolved permission strings for this user's role (frontend gating).
    permissions: list[str] = []
    created_at: datetime
    updated_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6)
    full_name: str = Field(default="", max_length=160)
    role: Role = Role.VIEWER


class SignupRequest(BaseModel):
    """Self-service registration: provisions a brand-new organization (company)
    and its first user, who becomes that org's ADMIN."""

    company_name: str = Field(..., min_length=1, max_length=160)
    full_name: str = Field(default="", max_length=160)
    email: EmailStr
    password: str = Field(..., min_length=6)
