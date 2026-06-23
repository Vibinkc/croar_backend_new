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
from sqlalchemy import select

from app.core.dependencies import DBSessionDep, PermissionChecker, get_current_user
from app.models.enterprise.employee import Employee
from app.models.enterprise.user_role import EnterpriseUser
from app.models.shared.constants import ModuleScope, PermissionAction
from app.models.shared.super_admin import SuperAdmin
from app.payroll.constants import Permission

# Re-exported so payroll code can keep importing it from this module.
__all__ = [
    "CurrentUserDep",
    "DBSessionDep",
    "get_current_company_id",
    "get_current_employee_id",
    "require_permission",
]

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


async def get_current_employee_id(current_user: CurrentUserDep, db: DBSessionDep) -> uuid.UUID:
    """Resolve the signed-in user's own Employee id for self-service (``/me``).

    Croar's user has no explicit employee link, so we match the employee by email
    within the user's company (the common case: an employee logs in with the same
    email as their employee record). 404 if no matching employee exists.
    """
    company_id = get_current_company_id(current_user)
    email = getattr(current_user, "email", None)
    emp_id: uuid.UUID | None = None
    if email:
        emp_id = (
            await db.execute(
                select(Employee.id).where(
                    Employee.email == email, Employee.company_id == company_id, Employee.deleted_at.is_(None)
                )
            )
        ).scalar_one_or_none()
    if emp_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No employee record is linked to your account."
        )
    return emp_id


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


def require_permission(permission: Permission):
    """Dependency factory enforcing a ``payroll:*`` permission.

    Delegates to Croar's ``PermissionChecker`` (returns the current user when
    authorized, raises 403 otherwise) so the call sites in the payroll routers
    stay exactly as the teammate wrote them.

    ``SELF_READ`` is special: the ``/me`` self-service routes are already scoped to
    the caller's own employee record, so they only require authentication (any
    logged-in user), not a tenant RBAC permission — mirroring the original module
    where every EMPLOYEE user holds ``self:read`` by default.
    """
    if permission is Permission.SELF_READ:

        def _authenticated_only(current_user: CurrentUserDep) -> CurrentUser:
            return current_user

        return _authenticated_only

    module, action = _PERM_MAP[permission]
    return PermissionChecker(module, action)
