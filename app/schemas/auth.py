from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.shared.constants import ModuleScope, PermissionAction


class Token(BaseModel):
    """Token response with access and refresh tokens."""

    access_token: str
    refresh_token: str
    token_type: str
    role: str
    expires_in: int  # seconds until access token expires


class TokenData(BaseModel):
    """Data extracted from JWT token."""

    email: str | None = None
    role: str | None = None


class RefreshTokenRequest(BaseModel):
    """Request to refresh access token."""

    refresh_token: str


class LogoutRequest(BaseModel):
    """Request to logout and blacklist token."""

    pass  # Token comes from Authorization header


class PermissionSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    resource: str
    module: ModuleScope
    action: PermissionAction
    description: str | None = None


class RoleSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None = None
    is_system: bool
    role_rank: int
    permissions: list[PermissionSchema] = []


class UserInTeam(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    first_name: str | None = None
    last_name: str | None = None
    roles: list[RoleSchema] = []


class EnterpriseSignUpRequest(BaseModel):
    email: str
    password: str
    first_name: str
    last_name: str
    company_name: str


class EnterpriseSignUpResponse(BaseModel):
    message: str
    user_id: UUID
    company_id: UUID
