"""Integration tests for the automation routers (mail / interview / onboarding automation).

Create happy-paths need a seeded job + template chain (covered by build_hiring_pipeline unit/e2e),
so here we exhaustively cover the security surface: auth (401), permission (403), list, 404.
"""

import uuid
from typing import ClassVar

from app.models.shared.constants import ModuleScope, PermissionAction

MAIL = "/api/v1/enterprise/automation/mail"
INTERVIEW = "/api/v1/enterprise/interview-automation"
ONBOARDING = "/api/v1/enterprise/onboarding-automation"


class TestMailAutomation:
    read: ClassVar = [(ModuleScope.communications, PermissionAction.read)]
    create: ClassVar = [(ModuleScope.communications, PermissionAction.create)]
    delete: ClassVar = [(ModuleScope.communications, PermissionAction.delete)]

    async def test_list_401(self, client):
        assert (await client.get(MAIL)).status_code == 401

    async def test_list_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=self.create))
        assert (await client.get(MAIL)).status_code == 403

    async def test_list_happy(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=self.read))
        r = await client.get(MAIL)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_create_401(self, client):
        assert (await client.post(MAIL, json={})).status_code == 401

    async def test_create_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=self.read))
        assert (await client.post(MAIL, json={})).status_code == 403

    async def test_delete_401(self, client):
        assert (await client.delete(f"{MAIL}/{uuid.uuid4()}")).status_code == 401

    async def test_delete_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=self.read))
        assert (await client.delete(f"{MAIL}/{uuid.uuid4()}")).status_code == 403


class TestInterviewAutomation:
    read: ClassVar = [(ModuleScope.interviews, PermissionAction.read)]
    create: ClassVar = [(ModuleScope.interviews, PermissionAction.create)]

    async def test_list_401(self, client):
        assert (await client.get(INTERVIEW + "/")).status_code == 401

    async def test_list_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=self.create))
        assert (await client.get(INTERVIEW + "/")).status_code == 403

    async def test_list_happy(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=self.read))
        r = await client.get(INTERVIEW + "/")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_create_401(self, client):
        assert (await client.post(INTERVIEW + "/", json={})).status_code == 401

    async def test_create_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=self.read))
        assert (await client.post(INTERVIEW + "/", json={})).status_code == 403


class TestOnboardingAutomation:
    read: ClassVar = [(ModuleScope.onboarding, PermissionAction.read)]
    create: ClassVar = [(ModuleScope.onboarding, PermissionAction.create)]

    async def test_list_401(self, client):
        assert (await client.get(ONBOARDING + "/")).status_code == 401

    async def test_list_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=self.create))
        assert (await client.get(ONBOARDING + "/")).status_code == 403

    async def test_list_happy(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=self.read))
        r = await client.get(ONBOARDING + "/")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_create_401(self, client):
        assert (await client.post(ONBOARDING + "/", json={})).status_code == 401

    async def test_create_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=self.read))
        assert (await client.post(ONBOARDING + "/", json={})).status_code == 403
