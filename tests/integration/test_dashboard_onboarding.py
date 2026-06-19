"""Integration tests for the dashboard stats + enterprise onboarding endpoints."""

import uuid

from app.models.shared.constants import ModuleScope, PermissionAction

DASH = "/api/v1/enterprise/dashboard"
ONB = "/api/v1/enterprise/onboarding"
ORG_READ = [(ModuleScope.organization, PermissionAction.read)]
ONB_READ = [(ModuleScope.onboarding, PermissionAction.read)]
ONB_CREATE = [(ModuleScope.onboarding, PermissionAction.create)]


class TestDashboardStats:
    async def test_unauthenticated_401(self, client):
        assert (await client.get(f"{DASH}/stats")).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=[(ModuleScope.jobs, PermissionAction.read)]))
        assert (await client.get(f"{DASH}/stats")).status_code == 403

    async def test_happy_path(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=ORG_READ))
        r = await client.get(f"{DASH}/stats")
        assert r.status_code == 200
        assert "active_jobs" in r.json()


class TestOnboardingList:
    async def test_unauthenticated_401(self, client):
        assert (await client.get(ONB + "/")).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=ONB_CREATE))
        assert (await client.get(ONB + "/")).status_code == 403

    async def test_happy_path(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=ONB_READ))
        r = await client.get(ONB + "/")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_statuses_happy(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=ONB_READ))
        assert (await client.get(f"{ONB}/statuses")).status_code == 200


class TestOnboardingInitiate:
    async def test_unauthenticated_401(self, client):
        assert (await client.post(f"{ONB}/initiate", json={})).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=ONB_READ))  # read != create
        assert (
            await client.post(f"{ONB}/initiate", json={"application_id": str(uuid.uuid4())})
        ).status_code == 403
