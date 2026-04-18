import contextlib
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.settings import get_settings

_settings = get_settings()


class Base(DeclarativeBase):
    pass


class DatabaseManager:
    def __init__(self) -> None:
        self.url = self.get_url(_settings.db_name)
        self.engine = self._create_engine(self.url)
        self.sessionmaker = self._create_sessionmaker(self.engine)

    def get_url(self, db_name: str) -> str:
        return f"postgresql+asyncpg://{_settings.db_user}:{_settings.db_password}@{_settings.db_host}:{_settings.db_port}/{db_name}"

    def _create_engine(self, url: str) -> AsyncEngine:
        return create_async_engine(
            url,
            echo=_settings.debug,
            pool_size=20,
            max_overflow=40,
            pool_timeout=30,
            pool_recycle=3600,
            pool_pre_ping=True,
        )

    def _create_sessionmaker(self, engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
        return async_sessionmaker(
            autocommit=False, class_=AsyncSession, autoflush=False, bind=engine, expire_on_commit=False
        )

    @contextlib.asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    async def close_all(self) -> None:
        await self.engine.dispose()


db_manager = DatabaseManager()

# Backward Compatibility Exports
SessionManager = db_manager
DBSessionManager = db_manager
SQLALCHEMY_DATABASE_URL = db_manager.url


def get_engine(url: str, **kwargs: Any) -> AsyncEngine:
    return create_async_engine(url, **kwargs)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with db_manager.session() as session:
        yield session


async def get_management_db() -> AsyncIterator[AsyncSession]:
    """Alias for get_db."""
    async with db_manager.session() as session:
        yield session


async def get_enterprise_db() -> AsyncIterator[AsyncSession]:
    """Alias for get_db."""
    async with db_manager.session() as session:
        yield session


async def get_shared_db() -> AsyncIterator[AsyncSession]:
    """Alias for get_db."""
    async with db_manager.session() as session:
        yield session


async def get_db_connect() -> AsyncIterator[AsyncConnection]:
    async with db_manager.engine.connect() as conn:
        yield conn
