from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy.exc import SQLAlchemyError

from app.core.anthropic_llm import AIUnavailableError
from app.core.exceptions import AppException


def ai_unavailable_handler(request: Request, exc: AIUnavailableError) -> JSONResponse:
    """The AI provider refused or could not serve the call — report it as 503.

    Every AI feature used to degrade silently instead: an exhausted credit balance came
    back as a one-line job description or an empty assessment at HTTP 200, so nobody could
    tell a failure from a bad generation. 503 lets each UI show its existing error state.
    """
    msg = str(exc)
    detail = "AI generation is unavailable right now. Please try again."
    if "credit balance is too low" in msg.lower():
        detail = "The AI provider account is out of credit. Top it up and try again."
    elif "rate_limit" in msg.lower():
        detail = "The AI provider is rate limiting us. Please try again in a moment."
    logger.error(f"AIUnavailable: {msg[:300]} | Path: {request.url.path}")
    return JSONResponse(status_code=503, content={"success": False, "message": detail, "detail": detail})


def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    logger.error(f"AppException: {exc.message} | Detail: {exc.detail} | Path: {request.url.path}")
    return JSONResponse(
        status_code=exc.status_code, content={"success": False, "message": exc.message, "detail": exc.detail}
    )


def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Report a bad request as 422, including on fields the stdlib cannot serialise.

    ``exc.errors()`` echoes the offending value back under ``input``, and for a Decimal field
    that value is a ``Decimal`` — which ``json.dumps`` refuses. The handler then raised inside
    itself and the caller got a 500, so every money field in payroll turned a bad request into
    what looked like a broken server. ``jsonable_encoder`` is what FastAPI's own default handler
    uses; it knows Decimal, UUID, datetime and the rest.
    """
    errors = exc.errors()
    logger.warning(f"Validation Error | Path: {request.url.path} | Errors: {errors}")
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder({"success": False, "message": "Validation Failed", "detail": errors}),
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
