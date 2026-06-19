"""Integration tests for the Platform / Super-Admin router (/api/v1/super-admin).

Endpoints require platform:moderate.
"""

import uuid

from app.models.shared.constants import ModuleScope, PermissionAction

BASE = "/api/v1/super-admin"
PLATFORM = [(ModuleScope.platform, PermissionAction.moderate)]
WRONG = [(ModuleScope.organization, PermissionAction.read)]


class TestStats:
    async def test_unauthenticated_401(self, client):
        assert (await client.get(f"{BASE}/stats")).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=WRONG))
        assert (await client.get(f"{BASE}/stats")).status_code == 403

    async def test_happy_path(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=PLATFORM))
        assert (await client.get(f"{BASE}/stats")).status_code == 200


class TestTenants:
    async def test_unauthenticated_401(self, client):
        assert (await client.get(f"{BASE}/tenants")).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=WRONG))
        assert (await client.get(f"{BASE}/tenants")).status_code == 403

    async def test_happy_path_lists_companies(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=PLATFORM))
        r = await client.get(f"{BASE}/tenants")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_get_unknown_tenant_404(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=PLATFORM))
        assert (await client.get(f"{BASE}/tenants/{uuid.uuid4()}")).status_code == 404
