from fastapi import HTTPException, Request, status
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_size: int):
        super().__init__(app)
        self.max_size = max_size

    async def dispatch(self, request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH"):
            content_length = request.headers.get("content-length")
            if content_length:
                if int(content_length) > self.max_size:
                    logger.warning(f"Request body too large: {content_length} bytes (Limit: {self.max_size})")
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Request body too large. Maximum allowed size is {self.max_size} bytes.",
                    )
        return await call_next(request)
