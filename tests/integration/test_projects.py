"""Exhaustive integration tests for the Projects router (/api/v1/enterprise/projects)."""

import uuid

from app.models.shared.constants import ModuleScope, PermissionAction

BASE = "/api/v1/enterprise/projects"
READ = [(ModuleScope.projects, PermissionAction.read)]
CREATE = [(ModuleScope.projects, PermissionAction.create)]
DELETE = [(ModuleScope.projects, PermissionAction.delete)]


async def _create_project(client, company_id, name="Apollo"):
    return await client.post(BASE + "/", json={"name": name, "company_id": str(company_id)})


class TestCreateProject:
    async def test_unauthenticated_401(self, client, seed_company):
        assert (await _create_project(client, seed_company.id)).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=READ))
        assert (await _create_project(client, seed_company.id)).status_code == 403

    async def test_happy_path(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=CREATE))
        r = await _create_project(client, seed_company.id, "Project X")
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "Project X"

    async def test_validation_missing_name_422(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=CREATE))
        r = await client.post(BASE + "/", json={"company_id": str(seed_company.id)})
        assert r.status_code == 422


class TestListProjects:
    async def test_unauthenticated_401(self, client):
        assert (await client.get(BASE + "/")).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=CREATE))
        assert (await client.get(BASE + "/")).status_code == 403

    async def test_happy_path(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=CREATE + READ))
        await _create_project(client, seed_company.id)
        r = await client.get(BASE + "/")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


class TestGetProject:
    async def test_unauthenticated_401(self, client):
        assert (await client.get(f"{BASE}/{uuid.uuid4()}")).status_code == 401

    async def test_not_found_404(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=READ))
        assert (await client.get(f"{BASE}/{uuid.uuid4()}")).status_code == 404

    async def test_happy_path(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=CREATE + READ))
        created = (await _create_project(client, seed_company.id, "Fetch")).json()
        r = await client.get(f"{BASE}/{created['id']}")
        assert r.status_code == 200
        assert r.json()["name"] == "Fetch"


class TestDeleteProject:
    async def test_unauthenticated_401(self, client):
        assert (await client.delete(f"{BASE}/{uuid.uuid4()}")).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=READ))
        assert (await client.delete(f"{BASE}/{uuid.uuid4()}")).status_code == 403

    async def test_not_found_404(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=DELETE))
        assert (await client.delete(f"{BASE}/{uuid.uuid4()}")).status_code == 404

    async def test_happy_path_204(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=CREATE + READ + DELETE))
        created = (await _create_project(client, seed_company.id, "Bye")).json()
        assert (await client.delete(f"{BASE}/{created['id']}")).status_code == 204
