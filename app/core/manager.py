from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.database import DBSessionManager


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """
    To handles startup and shutdown events.
    """
    yield
    if DBSessionManager.engine is not None:
        # Close the DB connection
        await DBSessionManager.close_all()
