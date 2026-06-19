"""Exhaustive integration tests for Assessment Templates (/api/v1/enterprise/assessment-templates)."""

import uuid

from app.models.shared.constants import ModuleScope, PermissionAction

BASE = "/api/v1/enterprise/assessment-templates"
READ = [(ModuleScope.assessments, PermissionAction.read)]
CREATE = [(ModuleScope.assessments, PermissionAction.create)]
DELETE = [(ModuleScope.assessments, PermissionAction.delete)]


async def _create(client, name="Aptitude Test"):
    return await client.post(BASE + "/", json={"name": name, "type": "BOTH", "topic": "Python"})


class TestCreate:
    async def test_unauthenticated_401(self, client):
        assert (await _create(client)).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=READ))
        assert (await _create(client)).status_code == 403

    async def test_happy_path(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=CREATE))
        r = await _create(client, "Coding Test")
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "Coding Test"

    async def test_validation_missing_topic_422(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=CREATE))
        assert (await client.post(BASE + "/", json={"name": "x", "type": "BOTH"})).status_code == 422


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
        assert r.json()["name"] == "Fetch"


class TestDelete:
    async def test_unauthenticated_401(self, client):
        assert (await client.delete(f"{BASE}/{uuid.uuid4()}")).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=READ))
        assert (await client.delete(f"{BASE}/{uuid.uuid4()}")).status_code == 403

    async def test_not_found_404(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=DELETE))
        assert (await client.delete(f"{BASE}/{uuid.uuid4()}")).status_code == 404

    async def test_happy_path_204(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=CREATE + DELETE))
        created = (await _create(client)).json()
        assert (await client.delete(f"{BASE}/{created['id']}")).status_code == 204
