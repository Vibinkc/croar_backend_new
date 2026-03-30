from app.core.database import Base

class SharedBase(Base):
    __abstract__ = True

# Import models to register them
from .super_admin import SuperAdmin
from .global_role import GlobalRole, super_admin_roles
from .audit_log import AuditLog
from .backup import Backup

__all__ = ["SharedBase", "SuperAdmin", "GlobalRole", "super_admin_roles", "AuditLog", "Backup"]

