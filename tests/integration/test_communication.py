"""Exhaustive integration tests for Email Templates (/api/v1/enterprise/communication/templates)."""

import uuid

from app.models.shared.constants import ModuleScope, PermissionAction

BASE = "/api/v1/enterprise/communication/templates"
READ = [(ModuleScope.communications, PermissionAction.read)]
CREATE = [(ModuleScope.communications, PermissionAction.create)]
UPDATE = [(ModuleScope.communications, PermissionAction.update)]
DELETE = [(ModuleScope.communications, PermissionAction.delete)]


def _tpl(**over):
    body = {"name": f"T-{uuid.uuid4().hex[:6]}", "subject": "Hi {{name}}", "body": "<p>Hello</p>"}
    body.update(over)
    return body


async def _create(client, **over):
    return await client.post(BASE, json=_tpl(**over))


class TestCreateTemplate:
    async def test_unauthenticated_401(self, client):
        assert (await _create(client)).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=READ))
        assert (await _create(client)).status_code == 403

    async def test_happy_path(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=CREATE))
        r = await _create(client, name="Welcome")
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "Welcome"

    async def test_validation_missing_subject_422(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=CREATE))
        body = _tpl()
        body.pop("subject")
        assert (await client.post(BASE, json=body)).status_code == 422


class TestListTemplates:
    async def test_unauthenticated_401(self, client):
        assert (await client.get(BASE)).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=CREATE))
        assert (await client.get(BASE)).status_code == 403

    async def test_happy_path(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=CREATE + READ))
        await _create(client)
        r = await client.get(BASE)
        assert r.status_code == 200
        assert isinstance(r.json(), list)


class TestUpdateTemplate:
    async def test_unauthenticated_401(self, client):
        assert (await client.patch(f"{BASE}/{uuid.uuid4()}", json={"subject": "x"})).status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=READ))
        assert (await client.patch(f"{BASE}/{uuid.uuid4()}", json={"subject": "x"})).status_code == 403

    async def test_not_found_404(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=UPDATE))
        assert (await client.patch(f"{BASE}/{uuid.uuid4()}", json={"subject": "x"})).status_code == 404

    async def test_happy_path(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=CREATE + UPDATE))
        created = (await _create(client)).json()
        r = await client.patch(f"{BASE}/{created['id']}", json={"subject": "Updated"})
        assert r.status_code == 200
        assert r.json()["subject"] == "Updated"


class TestDeleteTemplate:
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
        assert (await client.delete(f"{BASE}/{created['id']}")).status_code == 200
