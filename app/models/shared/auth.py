import uuid

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    Column,
    Enum,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import SharedBase
from .constants import ModuleScope, PermissionAction, PermissionScope

# Association Table for SuperAdmin and Role
super_admin_roles = Table(
    "super_admin_roles",
    SharedBase.metadata,
    Column(
        "super_admin_id",
        UUID(as_uuid=True),
        ForeignKey("super_admins.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("role_id", UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("assigned_at", TIMESTAMP, server_default=func.now()),
)

# Association Table for User and Role
user_roles = Table(
    "user_roles",
    SharedBase.metadata,
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("assigned_at", TIMESTAMP, server_default=func.now()),
)

# Association Table for Role and Permission
role_permissions = Table(
    "role_permissions",
    SharedBase.metadata,
    Column("role_id", UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "permission_id",
        UUID(as_uuid=True),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("assigned_at", TIMESTAMP, server_default=func.now()),
)


class Permission(SharedBase):
    __tablename__ = "permissions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    resource: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[PermissionAction] = mapped_column(Enum(PermissionAction), nullable=False)
    module: Mapped[ModuleScope] = mapped_column(Enum(ModuleScope), server_default=ModuleScope.platform.value)
    scope: Mapped[PermissionScope] = mapped_column(
        Enum(PermissionScope), server_default=PermissionScope.tenant.value
    )

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=True
    )
    is_system: Mapped[bool] = mapped_column(Boolean, server_default="false")

    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, server_default=func.now())

    # Relationships
    roles = relationship("Role", secondary=role_permissions, back_populates="permissions")

    __table_args__ = (
        UniqueConstraint("resource", "action", "module", "tenant_id", name="uq_permissions_res_act_mod_ten"),
    )


class Role(SharedBase):
    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, server_default="false")

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=True
    )
    role_rank: Mapped[int] = mapped_column(Integer, server_default="10", nullable=False)

    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    # Relationships
    permissions = relationship("Permission", secondary=role_permissions, back_populates="roles")
    users = relationship("EnterpriseUser", secondary=user_roles, back_populates="roles")
    super_admins = relationship("SuperAdmin", secondary=super_admin_roles, back_populates="roles")

    __table_args__ = (UniqueConstraint("name", "tenant_id", name="uq_roles_name_tenant"),)
