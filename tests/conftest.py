"""Shared pytest fixtures for the Croar backend test-suite.

These tests are intentionally DB-independent for the auth/authz/validation paths:
- Missing-token requests are rejected by the OAuth2 scheme *before* any DB query.
- Permission-denied is raised by `PermissionChecker` *before* the handler body runs.
- Validation errors we assert on happen before the first DB call.

For paths that would run a handler, `auth_as` also overrides `get_db` with a dummy
session so nothing touches a real database.
"""

import asyncio
import os
from types import SimpleNamespace

import pytest
from dotenv import load_dotenv

# Load env BEFORE importing the app (settings are read at import time).
load_dotenv(".env.test" if os.getenv("ENV_MODE") == "test" else ".env")

from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.main import app


def _make_user(perms=(), company_id="00000000-0000-0000-0000-000000000001"):
    """Build a stand-in authenticated user.

    `perms` is an iterable of (ModuleScope, PermissionAction) tuples that the fake
    user's single role will carry — exactly what `PermissionChecker` inspects.
    """
    roles = [SimpleNamespace(permissions=[SimpleNamespace(module=m, action=a) for m, a in perms])]
    return SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        email="tester@example.com",
        first_name="Test",
        last_name="User",
        company_id=company_id,
        roles=roles,
    )


@pytest.fixture
def make_user():
    """Factory fixture for building fake authenticated users."""
    return _make_user


@pytest.fixture
def client():
    # raise_server_exceptions=False -> a 500 is returned as a response, not re-raised,
    # so a single buggy endpoint can't abort the whole test run.
    return TestClient(app, raise_server_exceptions=False)


class _FakeResult:
    """Mimics the SQLAlchemy Result surface used by simple read endpoints."""

    def scalar_one_or_none(self):
        return None

    def scalar(self):
        return 0

    def scalars(self):
        return self

    def all(self):
        return []


class _FakeSession:
    async def execute(self, *_args: object, **_kwargs: object) -> _FakeResult:
        await asyncio.sleep(0)  # async to match AsyncSession.execute (awaited by the app)
        return _FakeResult()


@pytest.fixture
def fake_db():
    """Override get_db with an in-memory fake session (no Postgres) for read endpoints."""

    async def _gen():
        yield _FakeSession()

    app.dependency_overrides[get_db] = _gen
    yield
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def auth_as():
    """Run requests as a given fake user by overriding auth + DB dependencies.

    Usage:  auth_as(make_user(perms=[(ModuleScope.platform, PermissionAction.moderate)]))
    Overrides are torn down automatically after each test.
    """

    async def _fake_db():
        # A sentinel session; the asserted paths never actually query it.
        yield SimpleNamespace()

    def _apply(user):
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_db] = _fake_db
        return user

    yield _apply

    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)
