"""Integration tests for the Team router (/api/v1/enterprise/team).

All endpoints require employees:moderate (the team-management permission).
"""

import uuid

from app.models.shared.constants import ModuleScope, PermissionAction

BASE = "/api/v1/enterprise/team"
MANAGE = [(ModuleScope.employees, PermissionAction.moderate)]
WRONG = [(ModuleScope.employees, PermissionAction.read)]  # read != moderate


class TestRolesList:
    async def test_unauthenticated_401(self, client):
        assert (await client.get(f"{BASE}/roles")).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=WRONG))
        assert (await client.get(f"{BASE}/roles")).status_code == 403

    async def test_happy_path(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=MANAGE))
        r = await client.get(f"{BASE}/roles")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


class TestPermissionsList:
    async def test_unauthenticated_401(self, client):
        assert (await client.get(f"{BASE}/permissions")).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=WRONG))
        assert (await client.get(f"{BASE}/permissions")).status_code == 403

    async def test_happy_path(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=MANAGE))
        r = await client.get(f"{BASE}/permissions")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


class TestMembersList:
    async def test_unauthenticated_401(self, client):
        assert (await client.get(f"{BASE}/members")).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=WRONG))
        assert (await client.get(f"{BASE}/members")).status_code == 403

    async def test_happy_path(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=MANAGE))
        r = await client.get(f"{BASE}/members")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


class TestRoleMutations:
    async def test_create_role_401(self, client):
        assert (await client.post(f"{BASE}/roles", json={})).status_code == 401

    async def test_create_role_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=WRONG))
        assert (await client.post(f"{BASE}/roles", json={})).status_code == 403

    async def test_delete_role_401(self, client):
        assert (await client.delete(f"{BASE}/roles/{uuid.uuid4()}")).status_code == 401

    async def test_delete_role_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=WRONG))
        assert (await client.delete(f"{BASE}/roles/{uuid.uuid4()}")).status_code == 403
