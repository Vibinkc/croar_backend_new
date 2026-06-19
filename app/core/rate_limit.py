from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address

# A generous global default protects every endpoint from abuse/DoS without needing a
# per-route decorator. Tighter per-route limits (e.g. login) can be layered on later.
limiter = Limiter(key_func=get_remote_address, default_limits=["300/minute"])


def rate_limit_exceeded_handler(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=429, content={"success": False, "message": "Too many requests", "detail": str(exc)}
    )
