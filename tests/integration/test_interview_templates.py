"""Exhaustive integration tests for Interview Templates (/api/v1/enterprise/interview-templates)."""

import uuid

from app.models.shared.constants import ModuleScope, PermissionAction

BASE = "/api/v1/enterprise/interview-templates"
READ = [(ModuleScope.interviews, PermissionAction.read)]
CREATE = [(ModuleScope.interviews, PermissionAction.create)]
DELETE = [(ModuleScope.interviews, PermissionAction.delete)]


async def _create(client, title="Tech Screen"):
    return await client.post(BASE + "/", json={"title": title})


class TestCreate:
    async def test_unauthenticated_401(self, client):
        assert (await _create(client)).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=READ))
        assert (await _create(client)).status_code == 403

    async def test_happy_path(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=CREATE))
        r = await _create(client, "System Design")
        assert r.status_code == 200, r.text
        assert r.json()["title"] == "System Design"

    async def test_validation_missing_title_422(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=CREATE))
        assert (await client.post(BASE + "/", json={})).status_code == 422


class TestList:
    async def test_unauthenticated_401(self, client):
        assert (await client.get(BASE + "/")).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=CREATE))
        assert (await client.get(BASE + "/")).status_code == 403

    async def test_happy_path(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=CREATE + READ))
        await _create(client)
        r = await client.get(BASE + "/")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


class TestGet:
    async def test_unauthenticated_401(self, client):
        assert (await client.get(f"{BASE}/{uuid.uuid4()}")).status_code == 401

    async def test_not_found_404(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=READ))
        assert (await client.get(f"{BASE}/{uuid.uuid4()}")).status_code == 404

    async def test_happy_path(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=CREATE + READ))
        created = (await _create(client, "Fetch")).json()
        r = await client.get(f"{BASE}/{created['id']}")
        assert r.status_code == 200
        assert r.json()["title"] == "Fetch"


class TestDelete:
    async def test_unauthenticated_401(self, client):
        assert (await client.delete(f"{BASE}/{uuid.uuid4()}")).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=READ))
        assert (await client.delete(f"{BASE}/{uuid.uuid4()}")).status_code == 403

    async def test_not_found_404(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=DELETE))
        assert (await client.delete(f"{BASE}/{uuid.uuid4()}")).status_code == 404

    async def test_happy_path(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=CREATE + DELETE))
        created = (await _create(client)).json()
        assert (await client.delete(f"{BASE}/{created['id']}")).status_code in (200, 204)
