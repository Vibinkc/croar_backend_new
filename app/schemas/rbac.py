from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.shared.constants import ModuleScope, PermissionAction, PermissionScope


class PermissionOut(BaseModel):
    id: UUID
    resource: str
    action: PermissionAction
    module: ModuleScope
    scope: PermissionScope
    is_system: bool
    created_at: datetime

    class Config:
        from_attributes = True


class RoleBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    role_rank: int = 10


class RoleCreate(RoleBase):
    permission_ids: list[UUID] = []


class RoleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    role_rank: int | None = None
    permission_ids: list[UUID] | None = None


class RoleOut(RoleBase):
    id: UUID
    is_system: bool
    created_at: datetime
    updated_at: datetime
    permissions: list[PermissionOut] = []

    class Config:
        from_attributes = True
