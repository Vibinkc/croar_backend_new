"""Exhaustive integration tests for the Employees router (/api/v1/enterprise/employees)."""

import uuid

from app.models.shared.constants import ModuleScope, PermissionAction

BASE = "/api/v1/enterprise/employees"
READ = [(ModuleScope.employees, PermissionAction.read)]
CREATE = [(ModuleScope.employees, PermissionAction.create)]
UPDATE = [(ModuleScope.employees, PermissionAction.update)]
DELETE = [(ModuleScope.employees, PermissionAction.delete)]


def _emp_body(company_id, **over):
    sfx = uuid.uuid4().hex[:8]
    body = {
        "employee_id": f"EMP-{sfx}",
        "first_name": "Jane",
        "last_name": "Doe",
        "email": f"jane.{sfx}@example.com",
        "company_id": str(company_id),
    }
    body.update(over)
    return body


async def _create_emp(client, company_id, **over):
    return await client.post(BASE + "/", json=_emp_body(company_id, **over))


class TestCreateEmployee:
    async def test_unauthenticated_401(self, client, seed_company):
        assert (await _create_emp(client, seed_company.id)).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=READ))
        assert (await _create_emp(client, seed_company.id)).status_code == 403

    async def test_happy_path_201(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=CREATE))
        r = await _create_emp(client, seed_company.id)
        assert r.status_code == 201, r.text
        assert r.json()["first_name"] == "Jane"

    async def test_validation_missing_email_422(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=CREATE))
        body = _emp_body(seed_company.id)
        body.pop("email")
        assert (await client.post(BASE + "/", json=body)).status_code == 422


class TestListEmployees:
    async def test_unauthenticated_401(self, client):
        assert (await client.get(BASE + "/")).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=CREATE))
        assert (await client.get(BASE + "/")).status_code == 403

    async def test_happy_path(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=CREATE + READ))
        await _create_emp(client, seed_company.id)
        r = await client.get(BASE + "/")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


class TestGetEmployee:
    async def test_unauthenticated_401(self, client):
        assert (await client.get(f"{BASE}/{uuid.uuid4()}")).status_code == 401

    async def test_not_found_404(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=READ))
        assert (await client.get(f"{BASE}/{uuid.uuid4()}")).status_code == 404

    async def test_happy_path(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=CREATE + READ))
        created = (await _create_emp(client, seed_company.id)).json()
        r = await client.get(f"{BASE}/{created['id']}")
        assert r.status_code == 200
        assert r.json()["id"] == created["id"]


class TestDeleteEmployee:
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
        created = (await _create_emp(client, seed_company.id)).json()
        assert (await client.delete(f"{BASE}/{created['id']}")).status_code == 204
