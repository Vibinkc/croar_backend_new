"""Integration tests for surveys, 360 assessments, and simulation scenarios.

Security surface (auth/permission) + list happy paths. Complex create bodies are exercised
via their dedicated CRUD where simple; here we lock down access control.
"""

import uuid
from typing import ClassVar

from app.models.shared.constants import ModuleScope, PermissionAction

SURVEYS = "/api/v1/enterprise/surveys"
X360 = "/api/v1/enterprise/x360"
SIM = "/api/v1/enterprise/simulations/scenarios"


class TestSurveys:
    read: ClassVar = [(ModuleScope.surveys, PermissionAction.read)]
    create: ClassVar = [(ModuleScope.surveys, PermissionAction.create)]

    async def test_types_401(self, client):
        assert (await client.get(f"{SURVEYS}/types")).status_code == 401

    async def test_types_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=self.create))
        assert (await client.get(f"{SURVEYS}/types")).status_code == 403

    async def test_types_happy(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=self.read))
        assert (await client.get(f"{SURVEYS}/types")).status_code == 200

    async def test_templates_list_401(self, client):
        assert (await client.get(f"{SURVEYS}/templates")).status_code == 401

    async def test_templates_list_happy(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=self.read))
        r = await client.get(f"{SURVEYS}/templates")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_template_create_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=self.read))
        assert (await client.post(f"{SURVEYS}/templates", json={})).status_code == 403


class TestX360:
    read: ClassVar = [(ModuleScope.analytics, PermissionAction.read)]
    create: ClassVar = [(ModuleScope.analytics, PermissionAction.create)]

    async def test_questions_list_401(self, client):
        assert (await client.get(f"{X360}/questions")).status_code == 401

    async def test_questions_list_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=self.create))
        assert (await client.get(f"{X360}/questions")).status_code == 403

    async def test_questions_list_happy(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=self.read))
        r = await client.get(f"{X360}/questions")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_templates_list_happy(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=self.read))
        r = await client.get(f"{X360}/templates")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_question_create_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=self.read))
        assert (await client.post(f"{X360}/questions", json={})).status_code == 403


class TestSimulationScenarios:
    read: ClassVar = [(ModuleScope.assessments, PermissionAction.read)]
    create: ClassVar = [(ModuleScope.assessments, PermissionAction.create)]
    delete: ClassVar = [(ModuleScope.assessments, PermissionAction.delete)]

    async def test_list_401(self, client):
        assert (await client.get(SIM)).status_code == 401

    async def test_list_403(self, client, seed_company, as_user, auth_user):
        # read perm is assessments:read; jobs:read is the wrong scope
        as_user(auth_user(seed_company.id, perms=[(ModuleScope.jobs, PermissionAction.read)]))
        assert (await client.get(SIM)).status_code == 403

    async def test_list_happy(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=self.read))
        r = await client.get(SIM)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_create_401(self, client):
        assert (await client.post(SIM, json={})).status_code == 401

    async def test_create_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=self.read))
        assert (await client.post(SIM, json={})).status_code == 403

    async def test_delete_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=self.read))
        assert (await client.delete(f"{SIM}/{uuid.uuid4()}")).status_code == 403
