"""Integration tests for the Applications router (/api/v1/enterprise/applications).

Applications are gated by the `jobs` scope (they're part of the hiring pipeline).
"""

import uuid

from app.models.shared.constants import ModuleScope, PermissionAction

BASE = "/api/v1/enterprise/applications"
READ = [(ModuleScope.jobs, PermissionAction.read)]
MODERATE = [(ModuleScope.jobs, PermissionAction.moderate)]
DELETE = [(ModuleScope.jobs, PermissionAction.delete)]


class TestListApplications:
    async def test_unauthenticated_401(self, client):
        assert (await client.get(BASE + "/")).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=[(ModuleScope.candidates, PermissionAction.read)]))
        assert (await client.get(BASE + "/")).status_code == 403

    async def test_happy_path_empty_list(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=READ))
        r = await client.get(BASE + "/")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


class TestStages:
    async def test_unauthenticated_401(self, client):
        assert (await client.get(f"{BASE}/stages")).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=[(ModuleScope.candidates, PermissionAction.read)]))
        assert (await client.get(f"{BASE}/stages")).status_code == 403

    async def test_happy_path(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=READ))
        assert (await client.get(f"{BASE}/stages")).status_code == 200


class TestUpdateStage:
    async def test_unauthenticated_401(self, client):
        r = await client.patch(f"{BASE}/{uuid.uuid4()}/stage", json={"current_stage": 2})
        assert r.status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=READ))  # read != moderate
        r = await client.patch(f"{BASE}/{uuid.uuid4()}/stage", json={"current_stage": 2})
        assert r.status_code == 403


class TestBulkDelete:
    async def test_unauthenticated_401(self, client):
        assert (await client.request("DELETE", f"{BASE}/bulk", json={"ids": []})).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=READ))  # read != delete
        r = await client.request("DELETE", f"{BASE}/bulk", json={"ids": []})
        assert r.status_code == 403
