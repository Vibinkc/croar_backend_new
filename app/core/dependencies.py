from typing import Annotated, cast

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db, get_db_connect
from app.core.settings import get_settings
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
            selectinload(EnterpriseUser.company),
        )
        .where(EnterpriseUser.email == email)
    )
    result_eu = await session.execute(stmt_eu)
    user_eu = result_eu.scalar_one_or_none()

    if user_eu:
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
        from app.models.enterprise.company import Company

        stmt_company = select(Company.id).limit(1)
        res_company = await session.execute(stmt_company)
        first_company_id = res_company.scalar()
        # SuperAdmin has no company_id column; attach it dynamically for downstream scoping.
        user_sa.company_id = first_company_id
        return user_sa

    raise credentials_exception


# Deprecated alias for backward compatibility
# Legacy alias removed after RBAC migration


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
