from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import db_manager
from app.core.exception_handlers import (
    app_exception_handler,
    database_exception_handler,
    generic_exception_handler,
    validation_exception_handler,
)
from app.core.exceptions import AppException
from app.core.logging_config import setup_logging
from app.core.rate_limit import limiter, rate_limit_exceeded_handler
from app.core.settings import get_settings
from app.middleware.request_logging import request_logging_middleware
from app.middleware.request_size_limit import RequestSizeLimitMiddleware
from app.middleware.security import SecurityHeadersMiddleware
from app.router import agents, auth, enterprise, platform
from app.router.enterprise.payroll import router as payroll_router

# Setup Logging
setup_logging()
_settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup logic
    print("Croar Backend Starting...")

    yield
    # Shutdown logic
    await db_manager.close_all()
    print("Croar Backend Shutting Down...")


app = FastAPI(
    title=_settings.app_name,
    debug=_settings.debug,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# Rate limiting (SlowAPI): register the limiter + middleware so the configured limits
# actually take effect (previously the limiter was defined but never wired in).
from slowapi.middleware import SlowAPIMiddleware

app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

# Exception Handlers
app.add_exception_handler(AppException, app_exception_handler)  # type: ignore
app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore
app.add_exception_handler(SQLAlchemyError, database_exception_handler)  # type: ignore
app.add_exception_handler(Exception, generic_exception_handler)
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# Middleware
app.middleware("http")(request_logging_middleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestSizeLimitMiddleware, max_size=10 * 1024 * 1024)  # 10MB
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.parsed_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----- Payroll activity-trail middleware (ported from the payroll module) -----
# Records an audit row for every authenticated, mutating payroll API request.
# Best-effort: never breaks the response. Actor is read from Croar's JWT
# (user_id claim); company is backfilled from the user in audit_service.record.
import uuid as _uuid
from collections.abc import Awaitable as _Awaitable
from collections.abc import Callable as _Callable

from jose import jwt as _jose_jwt
from starlette.requests import Request as _Request
from starlette.responses import Response as _Response

from app.services.payroll import audit_service as _payroll_audit_service

_AUDIT_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_AUDIT_PATH_PREFIXES = (
    "/api/v1/enterprise/payroll",
    "/api/v1/enterprise/leave",
    "/api/v1/enterprise/timesheets",
    "/api/v1/enterprise/taxes",
    "/api/v1/enterprise/calendar",
    "/api/v1/enterprise/reports",
    "/api/v1/enterprise/settings",
)
# A read-only live calculation fired on every keystroke — not a real mutation.
_AUDIT_SKIP_PATHS = {"/api/v1/enterprise/payroll/structures/preview"}


@app.middleware("http")
async def _payroll_audit_requests(
    request: _Request, call_next: "_Callable[[_Request], _Awaitable[_Response]]"
) -> _Response:
    response = await call_next(request)
    try:
        method = request.method
        path = request.url.path
        if (
            method in _AUDIT_METHODS
            and path.startswith(_AUDIT_PATH_PREFIXES)
            and path not in _AUDIT_SKIP_PATHS
        ):
            actor_id: _uuid.UUID | None = None
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                try:
                    payload = _jose_jwt.decode(
                        auth_header[7:], _settings.secret_key, algorithms=[_settings.algorithm]
                    )
                    uid = payload.get("user_id")
                    actor_id = _uuid.UUID(uid) if uid else None
                except Exception:
                    actor_id = None
            if response.status_code < 400 or actor_id is not None:
                await _payroll_audit_service.record(
                    company_id=None,
                    actor_id=actor_id,
                    method=method,
                    path=path,
                    status_code=response.status_code,
                )
    except Exception:  # pragma: no cover - audit must never break a request
        pass
    return response


# Routers
app.include_router(auth.router, prefix="/api/v1")
app.include_router(enterprise.router, prefix="/api/v1/enterprise", tags=["Enterprise"])
app.include_router(platform.router, prefix="/api/v1/super-admin", tags=["Platform Admin"])
app.include_router(agents.router, prefix="/api/v1", tags=["Agent OS"])
# Payroll/HR module (sub-routers carry absolute /api/v1/enterprise/... prefixes)
app.include_router(payroll_router)

# Static Files
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": "Welcome to Croar API", "version": "1.0.0"}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# Alias for sourcing search to prevent 404s from legacy paths
from app.router.enterprise.sourcing import search_profiles as sourcing_search


@app.get("/search")
async def legacy_search(
    q: str, location: str | None = None, platform: str = "github", page: int = 1, page_size: int = 15
):
    return await sourcing_search(q, location, platform, page, page_size)
