import secrets
import uuid
from datetime import timedelta
from typing import Annotated, Any, cast

from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep, get_current_user
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_password_hash,
    verify_password,
)
from app.core.settings import get_settings
from app.models.enterprise.company import Company
from app.models.enterprise.user_role import EnterpriseUser
from app.models.shared.auth import Permission, Role
from app.models.shared.constants import ModuleScope
from app.models.shared.super_admin import SuperAdmin
from app.models.shared.system_settings import SystemSettings
from app.schemas.auth import EnterpriseSignUpRequest, EnterpriseSignUpResponse, RefreshTokenRequest, Token

_settings = get_settings()
router = APIRouter(prefix="/auth", tags=["Authentication"])


async def _provision_sso_org_and_user(
    session: AsyncSession,
    *,
    email: str,
    given_name: str,
    family_name: str,
    picture: str,
    owner_label: str,
    audit_action: str,
) -> EnterpriseUser:
    """First-time SSO login: create the company, an ADMIN role (all non-platform permissions) and
    the user, then audit-log and persist. Shared by the Google and Microsoft login flows."""
    import re

    from app.models.shared.audit_log import AuditLog

    company_name = f"{given_name or owner_label}'s Organization"
    base_slug = re.sub(r"[^a-z0-9]", "-", company_name.lower()).strip("-")
    company_slug = f"{base_slug}-{str(uuid.uuid4())[:8]}"

    new_company = Company(name=company_name, slug=company_slug)
    session.add(new_company)
    await session.flush()

    admin_role = Role(
        name="ADMIN",
        description=f"Administrator for {company_name}",
        is_system=True,
        tenant_id=new_company.id,
        role_rank=1,
    )
    perm_stmt = select(Permission).where(Permission.module != ModuleScope.platform)
    admin_role.permissions = list((await session.execute(perm_stmt)).scalars().all())
    session.add(admin_role)
    await session.flush()

    user_obj = EnterpriseUser(
        email=email,
        # SSO users authenticate via the IdP; store an unusable random hash.
        password_hash=get_password_hash(secrets.token_urlsafe(32)),
        first_name=given_name,
        last_name=family_name,
        profile_image=picture,
        company_id=new_company.id,
        is_active=True,
        is_self_registered=True,
    )
    user_obj.roles = [admin_role]
    session.add(user_obj)
    await session.flush()

    session.add(
        AuditLog(
            action=audit_action,
            entity_id=new_company.id,
            details={"email": email, "company_name": company_name},
        )
    )
    await session.commit()
    await session.refresh(user_obj)
    return user_obj


def _sso_token_response(
    email: str, user_obj: EnterpriseUser, role_name: str, user_type: str, provider: str
) -> Token:
    """Build the access/refresh token pair + Token response shared by the SSO login flows."""
    extra_claims: dict[str, object] = {
        "role": role_name,
        "user_id": str(user_obj.id),
        "user_type": user_type,
        "sso": provider,
    }
    return Token(
        access_token=create_access_token(subject=email, extra_claims=extra_claims),
        refresh_token=create_refresh_token(subject=email),
        token_type="bearer",
        role=role_name,
        expires_in=_settings.access_token_expire_minutes * 60,
    )


@router.post("/signup", response_model=EnterpriseSignUpResponse)
async def signup(signup_data: EnterpriseSignUpRequest, session: DBSessionDep) -> EnterpriseSignUpResponse:
    """
    Public signup endpoint for new organizations.
    Creates a Company, an Admin Role for that company, and the first Admin User.
    """
    import re

    # 0. Check if signup is enabled
    stmt_setting = select(SystemSettings).where(SystemSettings.key == "signup_enabled")
    signup_setting = (await session.execute(stmt_setting)).scalar_one_or_none()
    if signup_setting and signup_setting.value_bool is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Self-service registration is currently disabled by the platform administrator.",
        )

    # 1. Check if user already exists
    stmt_user = select(EnterpriseUser).where(EnterpriseUser.email == signup_data.email)
    existing_user = (await session.execute(stmt_user)).scalar_one_or_none()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="A user with this email already exists."
        )

    # 2. Create Company
    slug = re.sub(r"[^a-zA-Z0-9]", "-", signup_data.company_name.lower())
    slug = re.sub(r"-+", "-", slug).strip("-")

    # Ensure slug is unique
    stmt_slug = select(Company).where(Company.slug == slug)
    if (await session.execute(stmt_slug)).scalar_one_or_none():
        slug = f"{slug}-{uuid.uuid4().hex[:4]}"

    new_company = Company(name=signup_data.company_name, slug=slug)
    session.add(new_company)
    await session.flush()  # Get company ID

    # 3. Create ADMIN role for this company
    admin_role = Role(
        name="ADMIN",
        description=f"Administrator for {new_company.name}",
        tenant_id=new_company.id,
        is_system=True,
        role_rank=1,
    )

    # Assign all non-platform permissions to this role
    perm_stmt = select(Permission).where(Permission.module != ModuleScope.platform)
    perms = (await session.execute(perm_stmt)).scalars().all()
    admin_role.permissions = list(perms)

    session.add(admin_role)

    # 4. Create Admin User
    new_user = EnterpriseUser(
        email=signup_data.email,
        password_hash=get_password_hash(signup_data.password),
        first_name=signup_data.first_name,
        last_name=signup_data.last_name,
        company_id=new_company.id,
        is_active=True,
        is_self_registered=True,
    )
    new_user.roles = [admin_role]
    session.add(new_user)

    # Final commit
    await session.flush()
    user_id = new_user.id
    company_id = new_company.id

    # 5. Audit Log
    from app.models.shared.audit_log import AuditLog

    log = AuditLog(
        action="ORGANIZATION_SIGNUP",
        entity_id=company_id,
        details={
            "email": signup_data.email,
            "company_name": signup_data.company_name,
            "user_id": str(user_id),
        },
    )
    session.add(log)

    await session.commit()

    return EnterpriseSignUpResponse(
        message="Organization and admin user created successfully.",
        user_id=cast("uuid.UUID", user_id),
        company_id=cast("uuid.UUID", company_id),
    )


@router.post("/google-login", response_model=Token)
async def google_login(session: DBSessionDep, data: dict[str, Any] = Body(...)) -> Token:
    """
    Login using Google Identity Services credential (ID Token).
    """
    from google.auth.transport import requests
    from google.oauth2 import id_token

    credential = cast("str", data.get("credential"))
    if not credential:
        raise HTTPException(status_code=400, detail="Missing Google credential")

    # Check if Google SSO is enabled globally
    from app.models.shared.system_settings import SystemSettings

    stmt_sso = select(SystemSettings).where(SystemSettings.key == "google_sso_enabled")
    sso_setting = (await session.execute(stmt_sso)).scalar_one_or_none()
    if sso_setting and sso_setting.value_bool is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Google Single Sign-On is currently disabled by the platform administrator.",
        )

    if not _settings.google_client_id:
        raise HTTPException(status_code=500, detail="Google SSO is not configured on the server")

    try:
        # Verify the token
        try:
            idinfo = id_token.verify_oauth2_token(credential, requests.Request(), _settings.google_client_id)
        except ValueError as e:
            # If token is "too early" (clock skew), wait 1.5s and retry once
            if "Token used too early" in str(e):
                import asyncio

                await asyncio.sleep(1.5)
                idinfo = id_token.verify_oauth2_token(
                    credential, requests.Request(), _settings.google_client_id
                )
            else:
                raise e

        email = idinfo.get("email")
        if not email:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Google account has no email"
            )
        print(f"Token verified for email: {email}")

        # 1. Check for EnterpriseUser
        print("Checking EnterpriseUser...")
        stmt_ent = (
            select(EnterpriseUser)
            .options(selectinload(EnterpriseUser.roles))
            .where(EnterpriseUser.email == email)
        )
        user_ent = (await session.execute(stmt_ent)).scalar_one_or_none()

        user_obj: Any = None
        role_name = "USER"
        user_type = "enterprise"

        if user_ent:
            user_obj = user_ent
            role_name = user_ent.roles[0].name if user_ent.roles else "RECRUITER"
            user_type = "enterprise"
        else:
            # 2. Check for SuperAdmin
            print("Checking SuperAdmin...")
            from app.models.shared.super_admin import SuperAdmin

            stmt_admin = (
                select(SuperAdmin).options(selectinload(SuperAdmin.roles)).where(SuperAdmin.email == email)
            )
            user_admin = (await session.execute(stmt_admin)).scalar_one_or_none()

            if user_admin:
                user_obj = user_admin
                role_name = user_admin.roles[0].name if user_admin.roles else "SUPER_ADMIN"
                user_type = "superadmin"
            else:
                # 3. Fallback for Default Users
                from app.models.user import User as DefaultUser

                stmt_default = select(DefaultUser).where(DefaultUser.email == email)
                user_default = (await session.execute(stmt_default)).scalar_one_or_none()
                if user_default:
                    user_obj = user_default
                    role_name = "STUDENT"
                    user_type = "default"

        if not user_obj:
            # Check if signup is enabled
            stmt_setting = select(SystemSettings).where(SystemSettings.key == "signup_enabled")
            signup_setting = (await session.execute(stmt_setting)).scalar_one_or_none()
            if signup_setting and signup_setting.value_bool is False:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Self-service registration is currently disabled. Please contact the administrator.",
                )

            # --- AUTO-SIGNUP FLOW ---
            user_obj = await _provision_sso_org_and_user(
                session,
                email=email,
                given_name=idinfo.get("given_name", ""),
                family_name=idinfo.get("family_name", ""),
                picture=idinfo.get("picture", ""),
                owner_label="Google",
                audit_action="GOOGLE_SSO_AUTO_SIGNUP",
            )
            role_name = "ADMIN"
            user_type = "enterprise"

        if not user_obj.is_active:
            raise HTTPException(status_code=403, detail="Your account is currently disabled.")

        return _sso_token_response(email, user_obj, role_name, user_type, "google")

    except HTTPException as he:
        # Re-raise fastAPI HTTP exceptions
        raise he
    except ValueError as ve:
        # Invalid token
        print(f"Google Token Verification Failed: {ve}")
        raise HTTPException(status_code=401, detail=f"Invalid Google token: {ve}") from ve
    except Exception as e:
        print(f"Google Login Error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error during Google SSO") from e


@router.post("/token", response_model=Token)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()], session: DBSessionDep
) -> Token:
    """
    Login endpoint - returns access token and refresh token.
    Supports EnterpriseUser, SuperAdmin, and legacy DefaultUser.
    """
    from app.models.enterprise.user_role import EnterpriseUser as EntUser
    from app.models.user import User as DefaultUser

    # 0. Check if login is enabled (Global Guard)
    stmt_login_setting = select(SystemSettings).where(SystemSettings.key == "login_enabled")
    login_setting = (await session.execute(stmt_login_setting)).scalar_one_or_none()

    # If login is disabled, we still want to allow SuperAdmins to log in to fix it.

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

    # 1b. Enforce login toggle (SuperAdmins are exempt)
    if login_setting and login_setting.value_bool is False and user_type != "superadmin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform login is currently suspended for maintenance. Please try again later.",
        )

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


@router.post("/microsoft-login", response_model=Token)
async def microsoft_login(session: DBSessionDep, data: dict[str, Any] = Body(...)) -> Token:
    """
    Login using Microsoft Office 365 (Azure AD) ID Token.
    """
    import msal

    token = cast("str", data.get("token"))
    if not token:
        raise HTTPException(status_code=400, detail="Missing Microsoft token")

    print(f"DEBUG: Microsoft login attempt for token: {token[:20]}...")

    # Check if Microsoft SSO is enabled globally
    from app.models.shared.system_settings import SystemSettings

    stmt_sso = select(SystemSettings).where(SystemSettings.key == "microsoft_sso_enabled")
    sso_setting = (await session.execute(stmt_sso)).scalar_one_or_none()
    if sso_setting and sso_setting.value_bool is False:
        print("DEBUG: Microsoft SSO is disabled in settings")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Microsoft Office 365 SSO is currently disabled by the platform administrator.",
        )

    if not _settings.ms_client_id:
        print("DEBUG: MS_CLIENT_ID is missing in settings")
        raise HTTPException(status_code=500, detail="Microsoft SSO is not configured on the server")

    print(f"DEBUG: Using MS_CLIENT_ID: {_settings.ms_client_id}")

    # Verify Microsoft Token
    try:
        _client_app = msal.ConfidentialClientApplication(
            _settings.ms_client_id,
            client_credential=_settings.ms_client_secret,
            authority=f"https://login.microsoftonline.com/{_settings.ms_tenant_id}",
        )
        print("DEBUG: MSAL client app initialized")
    except Exception as e:
        print(f"DEBUG: MSAL initialization error: {e!s}")
        raise

    # Verify the ID token's SIGNATURE against Microsoft's published JWKS keys (fail closed).
    # Previously the signature was skipped entirely, which let anyone forge a token with a
    # known client-id `aud` and any email -> full authentication bypass.
    import jwt
    from jwt import PyJWKClient

    try:
        tenant = _settings.ms_tenant_id or "common"
        jwks_url = f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys"
        signing_key = PyJWKClient(jwks_url).get_signing_key_from_jwt(token)
        decoded_token = jwt.decode(
            token, signing_key.key, algorithms=["RS256"], audience=_settings.ms_client_id
        )

        # Defense in depth: re-check audience explicitly.
        if decoded_token.get("aud") != _settings.ms_client_id:
            raise HTTPException(status_code=401, detail="Invalid Microsoft token audience")

        email = (
            decoded_token.get("email") or decoded_token.get("preferred_username") or decoded_token.get("upn")
        )
        if not email:
            raise HTTPException(status_code=401, detail="Microsoft token missing email claim")

        print(f"Microsoft Token verified for email: {email}")

        # 1. Check for existing users (same logic as Google)
        stmt_ent = (
            select(EnterpriseUser)
            .options(selectinload(EnterpriseUser.roles))
            .where(EnterpriseUser.email == email)
        )
        user_ent = (await session.execute(stmt_ent)).scalar_one_or_none()

        user_obj: Any = None
        role_name = "USER"
        user_type = "enterprise"

        if user_ent:
            user_obj = user_ent
            role_name = user_ent.roles[0].name if user_ent.roles else "RECRUITER"
            user_type = "enterprise"
        else:
            # Check SuperAdmin
            from app.models.shared.super_admin import SuperAdmin

            stmt_admin = (
                select(SuperAdmin).options(selectinload(SuperAdmin.roles)).where(SuperAdmin.email == email)
            )
            user_admin = (await session.execute(stmt_admin)).scalar_one_or_none()
            if user_admin:
                user_obj = user_admin
                role_name = user_admin.roles[0].name if user_admin.roles else "SUPER_ADMIN"
                user_type = "superadmin"
            else:
                # Check Default User
                from app.models.user import User as DefaultUser

                stmt_default = select(DefaultUser).where(DefaultUser.email == email)
                user_default = (await session.execute(stmt_default)).scalar_one_or_none()
                if user_default:
                    user_obj = user_default
                    role_name = "STUDENT"
                    user_type = "default"

        if not user_obj:
            # AUTO-SIGNUP FLOW
            stmt_setting = select(SystemSettings).where(SystemSettings.key == "signup_enabled")
            signup_setting = (await session.execute(stmt_setting)).scalar_one_or_none()
            if signup_setting and signup_setting.value_bool is False:
                raise HTTPException(status_code=403, detail="Self-service registration is disabled.")

            user_obj = await _provision_sso_org_and_user(
                session,
                email=email,
                given_name=decoded_token.get("given_name", ""),
                family_name=decoded_token.get("family_name", ""),
                picture=decoded_token.get("picture", ""),
                owner_label="Microsoft",
                audit_action="MICROSOFT_SSO_AUTO_SIGNUP",
            )
            role_name = "ADMIN"
            user_type = "enterprise"

        if not user_obj.is_active:
            raise HTTPException(status_code=403, detail="Your account is currently disabled.")

        return _sso_token_response(email, user_obj, role_name, user_type, "microsoft")
    except Exception as e:
        print(f"Microsoft SSO Error: {e!s}")
        raise HTTPException(status_code=401, detail=f"Invalid Microsoft token: {e!s}") from e


@router.post("/forgot-password")
async def forgot_password(session: DBSessionDep, data: dict[str, str] = Body(...)) -> dict[str, str]:
    email = data.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Email is required")

    # Check if user exists (any user type)
    from app.models.enterprise.user_role import EnterpriseUser

    stmt = select(EnterpriseUser).where(EnterpriseUser.email == email)
    user = (await session.execute(stmt)).scalar_one_or_none()

    if user:
        # Generate a short-lived reset token (15 mins)
        reset_token = create_access_token(
            subject=email, extra_claims={"type": "reset_password"}, expires_delta=timedelta(minutes=15)
        )

        # In a real app, send an email here.
        # For now, we'll print it to the console for development.
        reset_link = f"http://localhost:3000/enterprise/reset-password?token={reset_token}"
        print(f"\n[PASSWORD RESET] Link for {email}:\n{reset_link}\n")

    # Always return success to prevent email enumeration
    return {"message": "If an account exists, a reset link has been sent."}


@router.post("/reset-password")
async def reset_password(session: DBSessionDep, data: dict[str, str] = Body(...)) -> dict[str, str]:
    token = data.get("token")
    new_password = data.get("new_password")

    if not token or not new_password:
        raise HTTPException(status_code=400, detail="Token and new password are required")

    try:
        payload = decode_token(token)
        if payload.get("type") != "reset_password":
            raise HTTPException(status_code=401, detail="Invalid reset token type")

        email = payload.get("sub")

        # Update User
        from app.models.enterprise.user_role import EnterpriseUser

        stmt = select(EnterpriseUser).where(EnterpriseUser.email == email)
        user = (await session.execute(stmt)).scalar_one_or_none()

        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        user.password_hash = get_password_hash(new_password)
        await session.commit()

        return {"message": "Password updated successfully"}
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired reset token") from None


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
