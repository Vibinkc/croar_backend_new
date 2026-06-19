"""Integration tests for the auth endpoints (/api/v1/auth)."""

import uuid

import pytest_asyncio

from app.core.security import get_password_hash

BASE = "/api/v1/auth"
# Generated per run (not hardcoded) so the password is real-but-unpredictable for the tests.
LOGIN_PHRASE = f"Pw-{uuid.uuid4().hex}"
WRONG_LOGIN_PHRASE = f"Pw-{uuid.uuid4().hex}"


@pytest_asyncio.fixture
async def seed_login_user(db_session, seed_company):
    """A login-capable EnterpriseUser with a known password."""
    from app.models.enterprise.user_role import EnterpriseUser

    user = EnterpriseUser(
        email="login@test.com",
        password_hash=get_password_hash(LOGIN_PHRASE),
        first_name="Log",
        last_name="In",
        is_active=True,
        company_id=seed_company.id,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


class TestToken:
    async def test_bad_credentials_401(self, client):
        r = await client.post(
            BASE + "/token", data={"username": "nope@x.com", "password": WRONG_LOGIN_PHRASE}
        )
        assert r.status_code == 401

    async def test_wrong_password_401(self, client, seed_login_user):
        r = await client.post(
            BASE + "/token", data={"username": "login@test.com", "password": WRONG_LOGIN_PHRASE}
        )
        assert r.status_code == 401

    async def test_happy_path_returns_token(self, client, seed_login_user):
        r = await client.post(BASE + "/token", data={"username": "login@test.com", "password": LOGIN_PHRASE})
        assert r.status_code == 200, r.text
        assert r.json().get("access_token")


class TestMe:
    async def test_unauthenticated_401(self, client):
        assert (await client.get(BASE + "/me")).status_code == 401

    async def test_happy_path_with_token(self, client, seed_login_user):
        tok = (
            await client.post(BASE + "/token", data={"username": "login@test.com", "password": LOGIN_PHRASE})
        ).json()["access_token"]
        r = await client.get(BASE + "/me", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200
        assert r.json()["email"] == "login@test.com"


class TestRefresh:
    async def test_garbage_token_rejected(self, client):
        r = await client.post(BASE + "/refresh", json={"refresh_token": "not.a.jwt"})
        assert r.status_code in (401, 422)


class TestForgotPassword:
    async def test_unknown_email_does_not_500(self, client):
        r = await client.post(BASE + "/forgot-password", json={"email": "ghost@nowhere.com"})
        # Either accepted silently (anti-enumeration) or a clean 404 — never a 500.
        assert r.status_code in (200, 202, 404)
