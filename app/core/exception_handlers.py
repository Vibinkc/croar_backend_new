from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy.exc import SQLAlchemyError

from app.core.exceptions import AppException


def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    logger.error(f"AppException: {exc.message} | Detail: {exc.detail} | Path: {request.url.path}")
    return JSONResponse(
        status_code=exc.status_code, content={"success": False, "message": exc.message, "detail": exc.detail}
    )


def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    logger.warning(f"Validation Error | Path: {request.url.path} | Errors: {exc.errors()}")
    return JSONResponse(
        status_code=422, content={"success": False, "message": "Validation Failed", "detail": exc.errors()}
    )


def database_exception_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.error(f"Database Error | Path: {request.url.path} | Error: {exc!s}")
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "message": "A database error occurred",
            "detail": str(exc) if request.app.debug else None,
        },
    )


def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(f"Unhandled Exception | Path: {request.url.path}")
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "message": "An internal server error occurred",
            "detail": str(exc) if request.app.debug else None,
        },
    )
