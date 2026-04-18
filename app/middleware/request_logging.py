import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from loguru import logger


async def request_logging_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id

    start_time = time.time()

    logger.info(f"Request started | ID: {request_id} | Path: {request.url.path} | Method: {request.method}")

    try:
        response = await call_next(request)
        process_time = time.time() - start_time
        response.headers["X-Process-Time"] = str(process_time)
        response.headers["X-Request-ID"] = request_id

        logger.info(
            f"Request completed | ID: {request_id} | Status: {response.status_code} | "
            f"Time: {process_time:.4f}s"
        )
        return response
    except Exception as e:
        process_time = time.time() - start_time
        logger.error(f"Request failed | ID: {request_id} | Error: {e!s} | Time: {process_time:.4f}s")
        raise
