from typing import TYPE_CHECKING, Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep, get_current_user
from app.core.security import create_access_token, create_refresh_token, decode_token, verify_password
from app.core.settings import get_settings
from app.schemas.auth import RefreshTokenRequest, Token

if TYPE_CHECKING:
    from app.models.shared.auth import Role

_settings = get_settings()
router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/token", response_model=Token)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()], session: DBSessionDep
) -> Token:
    """
    Login endpoint - returns access token and refresh token.
    Supports EnterpriseUser, SuperAdmin, and legacy DefaultUser.
    """
    from app.models.enterprise.user_role import EnterpriseUser as EntUser
    from app.models.shared.super_admin import SuperAdmin
    from app.models.user import User as DefaultUser

    user_obj: Any = None
    role_name = "USER"
    user_type = "enterprise"

    # 1. Try to fetch Enterprise User
    stmt_ent = select(EntUser).options(selectinload(EntUser.roles)).where(EntUser.email == form_data.username)
    result_ent = await session.execute(stmt_ent)
    user_ent = result_ent.scalar_one_or_none()

    if user_ent:
        user_obj = user_ent
        role_name = user_ent.roles[0].name if user_ent.roles else "RECRUITER"
        user_type = "enterprise"
    else:
        # 2. Try to fetch SuperAdmin
        stmt_admin = (
            select(SuperAdmin)
            .options(selectinload(SuperAdmin.roles))
            .where(SuperAdmin.email == form_data.username)
        )
        result_admin = await session.execute(stmt_admin)
        user_admin = result_admin.scalar_one_or_none()

        if user_admin:
            user_obj = user_admin
            role_name = user_admin.roles[0].name if user_admin.roles else "SUPER_ADMIN"
            user_type = "superadmin"
        else:
            # 3. Fallback for default users (Students)
            stmt_default = select(DefaultUser).where(DefaultUser.email == form_data.username)
            result_default = await session.execute(stmt_default)
            user_default = result_default.scalar_one_or_none()
            if user_default:
                user_obj = user_default
                role_name = "STUDENT"
                user_type = "default"

    if not user_obj:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check password
    hashed_password = cast(
        "str | None", getattr(user_obj, "password_hash", getattr(user_obj, "password", None))
    )
    if not hashed_password or not verify_password(form_data.password, hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Create claims
    extra_claims: dict[str, object] = {"role": role_name, "user_id": str(user_obj.id), "user_type": user_type}

    email = cast("str", user_obj.email)
    access_token = create_access_token(subject=email, extra_claims=extra_claims)
    refresh_token = create_refresh_token(subject=email)

    return Token(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        role=role_name,
        expires_in=_settings.access_token_expire_minutes * 60,
    )


@router.post("/refresh", response_model=Token)
async def refresh_token(refresh_data: RefreshTokenRequest, session: DBSessionDep) -> Token:
    try:
        payload = decode_token(refresh_data.refresh_token)
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")

        email = cast("str", payload.get("sub"))

        # Determine actual user level
        from app.models.enterprise.user_role import EnterpriseUser as EntUser
        from app.models.shared.super_admin import SuperAdmin
        from app.models.user import User as DefaultUser

        user_obj: Any = None
        role_name = "SUPER_ADMIN"
        user_type = "superadmin"

        stmt_admin = (
            select(SuperAdmin).options(selectinload(SuperAdmin.roles)).where(SuperAdmin.email == email)
        )
        user_admin = (await session.execute(stmt_admin)).scalar_one_or_none()

        if user_admin:
            user_obj = user_admin
            role_name = user_admin.roles[0].name if user_admin.roles else "SUPER_ADMIN"
            user_type = "superadmin"
        else:
            stmt_ent = select(EntUser).options(selectinload(EntUser.roles)).where(EntUser.email == email)
            user_ent = (await session.execute(stmt_ent)).scalar_one_or_none()
            if user_ent:
                user_obj = user_ent
                role_name = user_ent.roles[0].name if user_ent.roles else "RECRUITER"
                user_type = "enterprise"
            else:
                stmt_default = select(DefaultUser).where(DefaultUser.email == email)
                user_default = (await session.execute(stmt_default)).scalar_one_or_none()
                if user_default:
                    user_obj = user_default
                    role_name = "STUDENT"
                    user_type = "default"

        if not user_obj:
            raise HTTPException(status_code=401, detail="User no longer exists")

        extra_claims: dict[str, object] = {
            "role": role_name,
            "user_id": str(user_obj.id),
            "user_type": user_type,
        }

        access_token = create_access_token(subject=email, extra_claims=extra_claims)
        return Token(
            access_token=access_token,
            refresh_token=refresh_data.refresh_token,
            token_type="bearer",
            role=role_name,
            expires_in=_settings.access_token_expire_minutes * 60,
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid refresh token") from None


@router.get("/me")
async def get_me(current_user: Annotated[object, Depends(get_current_user)]) -> dict[str, object]:
    """
    Get current user profile and aggregated permissions.
    """

    permissions = []
    roles = cast("list[Role]", getattr(current_user, "roles", []))
    for role in roles:
        for perm in role.permissions:
            # We use module:action as the standard permission string
            permissions.append(f"{perm.module}:{perm.action}")

    # Remove duplicates
    unique_permissions = list(set(permissions))

    email = cast("str", getattr(current_user, "email", ""))
    user_id = cast("str", getattr(current_user, "id", ""))
    first_name = cast("str", getattr(current_user, "first_name", ""))
    last_name = cast("str", getattr(current_user, "last_name", ""))
    company_id = cast("str", getattr(current_user, "company_id", ""))

    return {
        "id": user_id,
        "email": email,
        "first_name": first_name,
        "last_name": last_name,
        "role": roles[0].name if roles else "USER",
        "company_id": company_id,
        "permissions": unique_permissions,
    }
