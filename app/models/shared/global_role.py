from sqlalchemy import String, Boolean, TIMESTAMP, func, ForeignKey, Table, Column, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text
from . import SharedBase

# Association Table
super_admin_roles = Table(
    "super_admin_roles",
    SharedBase.metadata,
    Column("super_admin_id", UUID(as_uuid=True), ForeignKey("super_admins.id", ondelete="CASCADE"), primary_key=True),
    Column("global_role_id", UUID(as_uuid=True), ForeignKey("global_roles.id", ondelete="CASCADE"), primary_key=True),
    Column("assigned_at", TIMESTAMP, server_default=func.now()),
)

class GlobalRole(SharedBase):
    __tablename__ = "global_roles"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()"))
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    permissions: Mapped[dict] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    is_system: Mapped[bool] = mapped_column(Boolean, default=True)
    
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    super_admins = relationship("SuperAdmin", secondary=super_admin_roles, back_populates="roles")
