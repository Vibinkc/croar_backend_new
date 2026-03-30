from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder

from app.core.dependencies import DBSessionDep
from app.core.security import (
    verify_password, 
    create_access_token, 
    create_refresh_token,
    decode_token
)
from app.core.settings import get_settings
from app.schemas.auth import Token, RefreshTokenRequest

_settings = get_settings()
router = APIRouter(prefix="/auth", tags=["Authentication"])

@router.post("/token", response_model=Token)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: DBSessionDep
):
    """
    Login endpoint - returns access token and refresh token.
    """
    # 1. Try to fetch Enterprise User
    from app.models.enterprise.user_role import EnterpriseUser as EntUser
    stmt = select(EntUser).where(EntUser.email == form_data.username).options(joinedload(EntUser.role))
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    
    if user:
        role_name = user.role.name if user.role else "RECRUITER"
    else:
        # Fallback for default users
        from app.models.user import User as DefaultUser
        stmt = select(DefaultUser).where(DefaultUser.email == form_data.username)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        role_name = "STUDENT"

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
    extra_claims = {
        "role": role_name,
        "user_id": str(user.id)
    }

    access_token = create_access_token(
        subject=user.email,
        extra_claims=extra_claims
    )
    refresh_token = create_refresh_token(subject=user.email)

    return Token(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        role=role_name,
        expires_in=_settings.access_token_expire_minutes * 60
    )

@router.post("/refresh", response_model=Token)
async def refresh_token(refresh_data: RefreshTokenRequest):
    # Simplified refresh for now (no blacklisting implementation yet)
    try:
        payload = decode_token(refresh_data.refresh_token)
        if payload.get("type") != "refresh":
             raise HTTPException(status_code=401, detail="Invalid token type")
        
        email = payload.get("sub")
        # In a real app, verify user still exists and isActive
        
        # Construct new access token with minimal claims or re-fetch
        access_token = create_access_token(subject=email, extra_claims={"role": "USER", "type": "access"})
        return Token(
            access_token=access_token,
            refresh_token=refresh_data.refresh_token,
            token_type="bearer",
            role="USER",
            expires_in=_settings.access_token_expire_minutes * 60
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
