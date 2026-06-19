"""Exhaustive integration tests for the Company router (/api/v1/enterprise/company).

Company management is multi-tenant: a normal user only sees their OWN company, so the
happy-path GET fetches `seed_company` as its owner.
"""

import uuid

from app.models.shared.constants import ModuleScope, PermissionAction

BASE = "/api/v1/enterprise/company"
READ = [(ModuleScope.organization, PermissionAction.read)]
CREATE = [(ModuleScope.organization, PermissionAction.create)]
DELETE = [(ModuleScope.organization, PermissionAction.delete)]


class TestCreateCompany:
    async def test_unauthenticated_401(self, client):
        assert (await client.post(BASE + "/", json={"name": "X"})).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=READ))
        assert (await client.post(BASE + "/", json={"name": "X"})).status_code == 403

    async def test_happy_path(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=CREATE))
        r = await client.post(BASE + "/", json={"name": "Partner Inc"})
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "Partner Inc"

    async def test_validation_missing_name_422(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=CREATE))
        assert (await client.post(BASE + "/", json={})).status_code == 422


class TestListCompanies:
    async def test_unauthenticated_401(self, client):
        assert (await client.get(BASE + "/")).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=CREATE))
        assert (await client.get(BASE + "/")).status_code == 403

    async def test_happy_path(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=READ))
        r = await client.get(BASE + "/")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


class TestGetCompany:
    async def test_unauthenticated_401(self, client):
        assert (await client.get(f"{BASE}/{uuid.uuid4()}")).status_code == 401

    async def test_not_found_404(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=READ))
        assert (await client.get(f"{BASE}/{uuid.uuid4()}")).status_code == 404

    async def test_happy_path_own_company(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=READ))
        r = await client.get(f"{BASE}/{seed_company.id}")
        assert r.status_code == 200
        assert r.json()["id"] == str(seed_company.id)

    async def test_other_company_not_found(self, client, seed_company, as_user, auth_user):
        # A user from a different company can't read seed_company.
        as_user(auth_user(uuid.uuid4(), perms=READ))
        assert (await client.get(f"{BASE}/{seed_company.id}")).status_code == 404


class TestDeleteCompany:
    async def test_unauthenticated_401(self, client):
        assert (await client.delete(f"{BASE}/{uuid.uuid4()}")).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=READ))
        assert (await client.delete(f"{BASE}/{uuid.uuid4()}")).status_code == 403

    async def test_not_found_404(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=DELETE))
        assert (await client.delete(f"{BASE}/{uuid.uuid4()}")).status_code == 404
