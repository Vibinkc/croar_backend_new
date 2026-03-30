from typing import Annotated, Optional, AsyncIterator, Any
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from jose import JWTError, jwt

from app.core.database import (
    db_manager,
    get_db,
    get_db_connect,
)
from app.core.settings import get_settings
from app.models.enterprise.user_role import EnterpriseUser as HiringAgent

_settings = get_settings()

# For ORM queries
DBSessionDep = Annotated[AsyncSession, Depends(get_db)]

# For Raw SQL queries
DBConnectionDep = Annotated[AsyncConnection, Depends(get_db_connect)]

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")

async def get_current_agent(
    token: Annotated[str, Depends(oauth2_scheme)],
    session: DBSessionDep,
) -> HiringAgent:
    """
    Get current authenticated agent from JWT token.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = jwt.decode(token, _settings.secret_key, algorithms=[_settings.algorithm])
        email: str = payload.get("sub")
        if email is None:
            print(f"DEBUG: Token payload missing 'sub': {payload}")
            raise credentials_exception
    except JWTError as e:
        print(f"DEBUG: JWT Decode Error: {e} | Token prefix: {token[:10]}...")
        raise credentials_exception
    
    stmt = select(HiringAgent).where(HiringAgent.email == email)
    result = await session.execute(stmt)
    agent = result.scalar_one_or_none()
    
    if agent is None:
        print(f"DEBUG: Agent not found for email: {email}")
        raise credentials_exception
    
    return agent

