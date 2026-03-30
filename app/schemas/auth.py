from pydantic import BaseModel

class Token(BaseModel):
    """Token response with access and refresh tokens"""
    access_token: str
    refresh_token: str
    token_type: str
    role: str
    expires_in: int  # seconds until access token expires


class TokenData(BaseModel):
    """Data extracted from JWT token"""
    email: str | None = None
    role: str | None = None


class RefreshTokenRequest(BaseModel):
    """Request to refresh access token"""
    refresh_token: str


class LogoutRequest(BaseModel):
    """Request to logout and blacklist token"""
    pass  # Token comes from Authorization header
