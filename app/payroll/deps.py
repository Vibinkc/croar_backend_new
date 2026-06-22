"""Auth compatibility shim for the ported payroll module.

The payroll code was written against its own ``app.core.dependencies`` (a
self-contained JWT + role system). When integrated into Croar we re-implement the
four symbols it depends on — ``DBSessionDep``, ``CurrentUserDep``,
``get_current_company_id`` and ``require_permission`` — on top of Croar's
existing auth (``get_current_user`` + ``PermissionChecker``), so the payroll
routers/services compile and run unchanged against a single login + user store.

Payroll's fine-grained ``payroll:*`` permissions are mapped onto Croar's
(ModuleScope, PermissionAction) RBAC model (see ``_PERM_MAP``).
"""

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.core.dependencies import DBSessionDep, PermissionChecker, get_current_user
from app.models.enterprise.user_role import EnterpriseUser
from app.models.shared.constants import ModuleScope, PermissionAction
from app.models.shared.super_admin import SuperAdmin
from app.payroll.constants import Permission

# Re-exported so payroll code can keep importing it from this module.
__all__ = ["CurrentUserDep", "DBSessionDep", "get_current_company_id", "require_permission"]

CurrentUser = EnterpriseUser | SuperAdmin
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def get_current_company_id(current_user: CurrentUserDep) -> uuid.UUID:
    """Multi-tenant scope, derived from the signed-in Croar user.

    Every payroll query filters by this id, so tenants only ever see their own
    data — identical contract to the original payroll dependency.
    """
    company_id = getattr(current_user, "company_id", None)
    if company_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="User is not associated with a company."
        )
    return company_id


# Map each payroll permission onto a Croar (module, action) pair. Croar's
# PermissionChecker grants access when any of the user's roles carries a
# permission with this module+action.
_PERM_MAP: dict[Permission, tuple[ModuleScope, PermissionAction]] = {
    Permission.PAYROLL_READ: (ModuleScope.payroll, PermissionAction.read),
    Permission.PAYROLL_CONFIGURE: (ModuleScope.payroll, PermissionAction.update),
    Permission.PAYROLL_RUN: (ModuleScope.payroll, PermissionAction.generate),
    Permission.PAYROLL_APPROVE: (ModuleScope.payroll, PermissionAction.review),
    Permission.PAYROLL_PAY: (ModuleScope.payroll, PermissionAction.finalize),
    Permission.PAYROLL_MANAGE: (ModuleScope.payroll, PermissionAction.delete),
    # Payroll user-administration maps onto org-level user management in Croar.
    Permission.USERS_MANAGE: (ModuleScope.organization, PermissionAction.create),
}


def require_permission(permission: Permission) -> PermissionChecker:
    """Dependency factory enforcing a ``payroll:*`` permission.

    Delegates to Croar's ``PermissionChecker`` (returns the current user when
    authorized, raises 403 otherwise) so the call sites in the payroll routers
    stay exactly as the teammate wrote them.
    """
    module, action = _PERM_MAP[permission]
    return PermissionChecker(module, action)
