"""Exhaustive integration tests for the Jobs router (/api/v1/enterprise/jobs).

Covers, per endpoint: 401 (no auth), 403 (wrong permission), happy path, validation,
404 (not found), and tenant isolation (can't touch another company's job).
"""

import uuid

from app.models.shared.constants import ModuleScope, PermissionAction

BASE = "/api/v1/enterprise/jobs"
JOBS_READ = [(ModuleScope.jobs, PermissionAction.read)]
JOBS_CREATE = [(ModuleScope.jobs, PermissionAction.create)]
JOBS_DELETE = [(ModuleScope.jobs, PermissionAction.delete)]


async def _create_job(client, title="Backend Engineer"):
    return await client.post(BASE + "/", json={"title": title, "description": "Build things."})


class TestCreateJob:
    async def test_unauthenticated_401(self, client):
        assert (await _create_job(client)).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=JOBS_READ))  # read != create
        assert (await _create_job(client)).status_code == 403

    async def test_happy_path_creates_job(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=JOBS_CREATE))
        r = await _create_job(client, "Senior Python Dev")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["title"] == "Senior Python Dev"
        assert body["id"]

    async def test_validation_missing_title_422(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=JOBS_CREATE))
        r = await client.post(BASE + "/", json={"description": "no title"})
        assert r.status_code == 422


class TestListJobs:
    async def test_unauthenticated_401(self, client):
        assert (await client.get(BASE + "/")).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=JOBS_CREATE))  # create != read
        assert (await client.get(BASE + "/")).status_code == 403

    async def test_lists_only_my_company_jobs(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=JOBS_CREATE + JOBS_READ))
        await _create_job(client, "Job A")
        r = await client.get(BASE + "/")
        assert r.status_code == 200
        assert "Job A" in [j["title"] for j in r.json()]


class TestGetJob:
    async def test_unauthenticated_401(self, client):
        assert (await client.get(f"{BASE}/{uuid.uuid4()}")).status_code == 401

    async def test_not_found_404(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=JOBS_READ))
        assert (await client.get(f"{BASE}/{uuid.uuid4()}")).status_code == 404

    async def test_happy_path(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=JOBS_CREATE + JOBS_READ))
        created = (await _create_job(client, "Fetch Me")).json()
        r = await client.get(f"{BASE}/{created['id']}")
        assert r.status_code == 200
        assert r.json()["title"] == "Fetch Me"

    async def test_tenant_isolation_404(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=JOBS_CREATE + JOBS_READ))
        created = (await _create_job(client, "Company A Job")).json()
        # look at company A's job as a DIFFERENT company -> not found
        as_user(auth_user(uuid.uuid4(), perms=JOBS_READ))
        assert (await client.get(f"{BASE}/{created['id']}")).status_code == 404


class TestDeleteJob:
    async def test_unauthenticated_401(self, client):
        assert (await client.delete(f"{BASE}/{uuid.uuid4()}")).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=JOBS_READ))  # read != delete
        assert (await client.delete(f"{BASE}/{uuid.uuid4()}")).status_code == 403

    async def test_not_found_404(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=JOBS_DELETE))
        assert (await client.delete(f"{BASE}/{uuid.uuid4()}")).status_code == 404

    async def test_happy_path_soft_deletes(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=JOBS_CREATE + JOBS_READ + JOBS_DELETE))
        created = (await _create_job(client, "Delete Me")).json()
        assert (await client.delete(f"{BASE}/{created['id']}")).status_code == 200
        assert (await client.get(f"{BASE}/{created['id']}")).status_code == 404
