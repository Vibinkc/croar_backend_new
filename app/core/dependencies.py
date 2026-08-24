from typing import Annotated, cast

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db, get_db_connect
from app.core.settings import get_settings
from app.models.enterprise.company import Company
from app.models.enterprise.user_role import EnterpriseUser
from app.models.shared.auth import Role
from app.models.shared.constants import ModuleScope, PermissionAction
from app.models.shared.super_admin import SuperAdmin

_settings = get_settings()

# For ORM queries
DBSessionDep = Annotated[AsyncSession, Depends(get_db)]

# For Raw SQL queries
DBConnectionDep = Annotated[AsyncConnection, Depends(get_db_connect)]

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")
# Same scheme but non-fatal: used by endpoints that work anonymously yet want to
# scope results to the caller's company when a valid token IS present.
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)], session: DBSessionDep
) -> EnterpriseUser | SuperAdmin:
    """
    Get current authenticated user (EnterpriseUser or SuperAdmin) from JWT token.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, _settings.secret_key, algorithms=[_settings.algorithm])
        email = cast("str | None", payload.get("sub"))
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception from None

    # 1. Check for EnterpriseUser first (most common)
    stmt_eu = (
        select(EnterpriseUser)
        .options(
            selectinload(EnterpriseUser.roles).selectinload(Role.permissions),
            # Load the company AND its parent so a sub-org's login can be blocked
            # when its parent consultancy is deactivated (cascade).
            selectinload(EnterpriseUser.company).selectinload(Company.parent),
        )
        .where(EnterpriseUser.email == email)
    )
    result_eu = await session.execute(stmt_eu)
    user_eu = result_eu.scalar_one_or_none()

    if user_eu:
        # Offboarding / tenant lifecycle enforcement: a disabled user, a user whose
        # organisation is deactivated or soft-deleted, OR a user whose parent
        # consultancy is deactivated (cascade), can no longer authenticate.
        if not user_eu.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Your account has been deactivated."
            )

        def _org_blocked(c: object | None) -> bool:
            return c is not None and (
                getattr(c, "deleted_at", None) is not None or not getattr(c, "is_active", True)
            )

        company = user_eu.company
        # A sub-organisation is suspended when its parent consultancy is.
        if _org_blocked(company) or _org_blocked(getattr(company, "parent", None)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Your organization has been deactivated."
            )
        # Set the billing context so AI usage during this request is metered to this company.
        try:
            from app.services.enterprise.credit_service import set_billing_context

            set_billing_context(getattr(user_eu, "company_id", None), getattr(user_eu, "id", None))
        except Exception:
            pass
        return user_eu

    # 2. Check for SuperAdmin
    stmt_sa = (
        select(SuperAdmin)
        .options(selectinload(SuperAdmin.roles).selectinload(Role.permissions))
        .where(SuperAdmin.email == email)
    )
    result_sa = await session.execute(stmt_sa)
    user_sa = result_sa.scalar_one_or_none()

    if user_sa:
        stmt_company = select(Company.id).limit(1)
        res_company = await session.execute(stmt_company)
        first_company_id = res_company.scalar()
        # SuperAdmin has no company_id column; attach it dynamically for downstream scoping.
        user_sa.company_id = first_company_id
        return user_sa

    raise credentials_exception


# Deprecated alias for backward compatibility
# Legacy alias removed after RBAC migration


async def get_optional_company_id(
    token: Annotated[str | None, Depends(oauth2_scheme_optional)], session: DBSessionDep
) -> str | None:
    """Best-effort company scoping for otherwise-anonymous endpoints.

    Returns the caller's `company_id` as a string when a valid token is present,
    otherwise `None`. Never raises — a missing/invalid token just means "no scope".
    """
    if not token:
        return None
    try:
        user = await get_current_user(token, session)
    except HTTPException:
        return None
    company_id = getattr(user, "company_id", None)
    return str(company_id) if company_id else None


class PermissionChecker:
    def __init__(self, module: ModuleScope, action: PermissionAction) -> None:
        self.module = module
        self.action = action

    def __call__(
        self, current_user: Annotated[EnterpriseUser | SuperAdmin, Depends(get_current_user)]
    ) -> EnterpriseUser | SuperAdmin:
        # SuperAdmins with the SUPER_ADMIN role often bypass checks or have all perms
        # In our seed script, we assigned all perms to the SUPER_ADMIN role.

        # Check all roles for the required permission
        has_permission = False
        roles = cast("list[Role]", current_user.roles)
        for role in roles:
            for perm in role.permissions:
                if perm.module == self.module and perm.action == self.action:
                    has_permission = True
                    break
            if has_permission:
                break

        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: {self.action} on {self.module}",
            )

        return current_user
