"""Integration-test fixtures: a real (SQLite in-memory) DB the endpoints run against.

The app's models are Postgres-flavoured (JSONB / ARRAY / UUID / uuid_generate_v4 defaults),
which SQLite can't build as-is. `_make_sqlite_compatible()` swaps those column TYPES for
SQLAlchemy's portable generics (JSON / Uuid) and drops the Postgres-only server defaults — so
`create_all` works AND data round-trips. This mutates the shared metadata, which is fine: the
test process never talks to a real Postgres.

A single in-memory DB is shared across connections via StaticPool.
"""

import uuid
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import ARRAY as GenericARRAY  # noqa: N811
from sqlalchemy import JSON, Uuid
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import DefaultClause
from sqlalchemy.types import ARRAY as TypesARRAY  # noqa: N811

from app.core.database import Base, get_db
from app.core.dependencies import get_current_user
from app.main import app


def _make_sqlite_compatible() -> None:
    for table in Base.metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, (JSONB, GenericARRAY, TypesARRAY)):
                col.type = JSON()
            elif isinstance(col.type, UUID):
                col.type = Uuid(as_uuid=True)
            sd = col.server_default
            if sd is not None:
                txt = str(getattr(sd, "arg", sd))
                if "uuid_generate_v4" in txt:
                    # Python-side default=uuid.uuid4 supplies the value on insert.
                    col.server_default = None
                elif "::" in txt:
                    # Postgres cast like '{}'::jsonb -> keep the literal for SQLite ('{}').
                    literal = txt.split("::", 1)[0].strip()
                    col.server_default = DefaultClause(sa_text(literal)) if literal else None


_make_sqlite_compatible()


@pytest_asyncio.fixture(scope="session")
async def db_engine():
    """One engine + schema per worker, built ONCE (not per test). The in-memory schema is
    expensive to create across 61 tables, so per-test isolation is done with a transaction
    rollback (see `_db_conn`) instead of rebuilding the schema every test."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # one shared connection -> one shared in-memory DB
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(db_engine):
    """Wipe every table after each test. The schema is built once (session-scoped engine);
    isolation is achieved by deleting rows between tests — far cheaper than recreating 61
    tables per test, and robust (no cross-session savepoint juggling on SQLite)."""
    yield
    async with db_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())


@pytest_asyncio.fixture
async def db_session(db_engine):
    """A session for seeding/asserting test data directly."""
    sm = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with sm() as session:
        yield session


@pytest_asyncio.fixture
async def override_db(db_engine):
    """Point the app's get_db at the SQLite test DB for the duration of a test."""
    sm = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async def _get_db():
        async with sm() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    yield
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def auth_user():
    """Return the factory that builds a stand-in authenticated user with chosen permissions."""
    return make_auth_user


def make_auth_user(company_id, perms=()):
    """Build a stand-in authenticated user with the given (ModuleScope, PermissionAction) perms."""
    roles = [SimpleNamespace(permissions=[SimpleNamespace(module=m, action=a) for m, a in perms])]
    return SimpleNamespace(
        id=uuid.uuid4(),
        email="tester@example.com",
        first_name="Test",
        last_name="User",
        company_id=company_id,
        roles=roles,
        company=SimpleNamespace(is_consultancy=False, name="Test Co"),
    )


@pytest_asyncio.fixture
async def seed_company(db_session):
    """Insert a company and return it (most endpoints are company-scoped)."""
    from app.models.enterprise.company import Company

    company = Company(name="Acme Test", slug=f"acme-{uuid.uuid4().hex[:8]}")
    db_session.add(company)
    await db_session.commit()
    await db_session.refresh(company)
    return company


@pytest_asyncio.fixture
async def client(override_db):
    """Async HTTP client bound to the app (sharing the SQLite test DB via override_db).

    Without calling `as_user`, requests are unauthenticated (good for 401 assertions).
    """
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def as_user():
    """Run requests as a fake user (override get_current_user). Auto-cleaned.

    Usage:  as_user(make_auth_user(company.id, perms=[(ModuleScope.jobs, PermissionAction.read)]))
    """

    def _apply(user):
        app.dependency_overrides[get_current_user] = lambda: user
        return user

    yield _apply
    app.dependency_overrides.pop(get_current_user, None)
