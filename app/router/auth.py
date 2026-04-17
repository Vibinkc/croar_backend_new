from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep, get_current_user
from app.core.security import create_access_token, create_refresh_token, decode_token, verify_password
from app.core.settings import get_settings
from app.schemas.auth import RefreshTokenRequest, Token

_settings = get_settings()
router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/token", response_model=Token)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()], session: DBSessionDep
):
    """
    Login endpoint - returns access token and refresh token.
    Supports EnterpriseUser, SuperAdmin, and legacy DefaultUser.
    """
    from app.models.enterprise.user_role import EnterpriseUser as EntUser
    from app.models.shared.super_admin import SuperAdmin
    from app.models.user import User as DefaultUser

    user = None
    role_name = "USER"
    user_type = "enterprise"

    # 1. Try to fetch Enterprise User
    stmt = select(EntUser).options(selectinload(EntUser.roles)).where(EntUser.email == form_data.username)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    if user:
        role_name = user.roles[0].name if user.roles else "RECRUITER"
        user_type = "enterprise"
    else:
        # 2. Try to fetch SuperAdmin
        stmt = (
            select(SuperAdmin)
            .options(selectinload(SuperAdmin.roles))
            .where(SuperAdmin.email == form_data.username)
        )
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

        if user:
            role_name = user.roles[0].name if user.roles else "SUPER_ADMIN"
            user_type = "superadmin"
        else:
            # 3. Fallback for default users (Students)
            stmt = select(DefaultUser).where(DefaultUser.email == form_data.username)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if user:
                role_name = "STUDENT"
                user_type = "default"

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check password
    hashed_password = getattr(user, "password_hash", getattr(user, "password", None))
    if not hashed_password or not verify_password(form_data.password, hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Create claims
    extra_claims = {"role": role_name, "user_id": str(user.id), "user_type": user_type}

    access_token = create_access_token(subject=user.email, extra_claims=extra_claims)
    refresh_token = create_refresh_token(subject=user.email)

    return Token(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        role=role_name,
        expires_in=_settings.access_token_expire_minutes * 60,
    )


@router.post("/refresh", response_model=Token)
async def refresh_token(refresh_data: RefreshTokenRequest, session: DBSessionDep):
    try:
        payload = decode_token(refresh_data.refresh_token)
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")

        email = payload.get("sub")

        # Determine actual user level
        from app.models.enterprise.user_role import EnterpriseUser as EntUser
        from app.models.shared.super_admin import SuperAdmin
        from app.models.user import User as DefaultUser

        stmt = select(SuperAdmin).options(selectinload(SuperAdmin.roles)).where(SuperAdmin.email == email)
        user = (await session.execute(stmt)).scalar_one_or_none()
        role_name = "SUPER_ADMIN"
        user_type = "superadmin"

        if not user:
            stmt = select(EntUser).options(selectinload(EntUser.roles)).where(EntUser.email == email)
            user = (await session.execute(stmt)).scalar_one_or_none()
            if user:
                role_name = user.roles[0].name if user.roles else "RECRUITER"
                user_type = "enterprise"
            else:
                stmt = select(DefaultUser).where(DefaultUser.email == email)
                user = (await session.execute(stmt)).scalar_one_or_none()
                if user:
                    role_name = "STUDENT"
                    user_type = "default"

        if not user:
            raise HTTPException(status_code=401, detail="User no longer exists")

        extra_claims = {"role": role_name, "user_id": str(user.id), "user_type": user_type}

        access_token = create_access_token(subject=email, extra_claims=extra_claims)
        return Token(
            access_token=access_token,
            refresh_token=refresh_data.refresh_token,
            token_type="bearer",
            role=role_name,
            expires_in=_settings.access_token_expire_minutes * 60,
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid refresh token")


@router.get("/me")
async def get_me(current_user: Annotated[Any, Depends(get_current_user)]):
    """
    Get current user profile and aggregated permissions.
    """
    permissions = []
    for role in current_user.roles:
        for perm in role.permissions:
            # We use module:action as the standard permission string
            permissions.append(f"{perm.module}:{perm.action}")

    # Remove duplicates
    unique_permissions = list(set(permissions))

    return {
        "id": str(current_user.id),
        "email": current_user.email,
        "first_name": getattr(current_user, "first_name", ""),
        "last_name": getattr(current_user, "last_name", ""),
        "role": current_user.roles[0].name if current_user.roles else "USER",
        "company_id": str(getattr(current_user, "company_id", "")),
        "permissions": unique_permissions,
    }
