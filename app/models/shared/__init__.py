from app.core.database import Base


class SharedBase(Base):
    __abstract__ = True


# Import models to register them
from .audit_log import AuditLog
from .auth import Permission, Role, role_permissions, super_admin_roles, user_roles
from .backup import Backup
from .super_admin import SuperAdmin

__all__ = [
    "AuditLog",
    "Backup",
    "Permission",
    "Role",
    "SharedBase",
    "SuperAdmin",
    "role_permissions",
    "super_admin_roles",
    "user_roles",
]
