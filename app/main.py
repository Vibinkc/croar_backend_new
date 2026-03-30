from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from sqlalchemy.exc import SQLAlchemyError

from app.core.exception_handlers import (
    app_exception_handler,
    database_exception_handler,
    generic_exception_handler,
    validation_exception_handler,
)
from app.core.exceptions import AppException
from app.core.logging_config import setup_logging
from app.core.rate_limit import rate_limit_exceeded_handler
from app.core.settings import get_settings
from app.middleware.request_logging import request_logging_middleware
from app.middleware.request_size_limit import RequestSizeLimitMiddleware
from app.middleware.security import SecurityHeadersMiddleware
from app.router import auth, enterprise

# Setup Logging
setup_logging()
_settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    print("Croar Backend Starting...")
    
    yield
    # Shutdown logic
    from app.core.database import db_manager
    await db_manager.close_all()
    print("Croar Backend Shutting Down...")


app = FastAPI(
    title=_settings.app_name,
    debug=_settings.debug,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json"
)

# Exception Handlers
app.add_exception_handler(AppException, app_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(SQLAlchemyError, database_exception_handler)
app.add_exception_handler(Exception, generic_exception_handler)
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# Middleware
app.middleware("http")(request_logging_middleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestSizeLimitMiddleware, max_size=10 * 1024 * 1024)  # 10MB
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth.router, prefix="/api/v1")
app.include_router(enterprise.router, prefix="/api/v1/enterprise", tags=["Enterprise"])

# Static Files
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


@app.get("/")
async def root():
    return {"message": "Welcome to Croar API", "version": "1.0.0"}


@app.get("/health")
async def health():
    return {"status": "ok"}
