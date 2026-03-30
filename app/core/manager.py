from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from app.core.database import DBSessionManager


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[Any]:  # noqa: ARG001
    """
    To handles startup and shutdown events.
    """
    yield
    if DBSessionManager.engine is not None:
        # Close the DB connection
        await DBSessionManager.close()
