"""Integration tests for the Agents / Croar Pilot router (/api/v1/agents).

External systems (the LangGraph LLM agent, MongoDB chat history, sourcing HTTP, SMTP) are
mocked so tests are fast and offline. Auth + validation cases need no mocks.
"""

import asyncio
import uuid

import app.router.agents as agents_mod

BASE = "/api/v1/agents"


class _FakeColl:
    """Stand-in for the Mongo pilot_chat_history collection."""

    def update_one(self, *a, **k):
        return None

    def find(self, *a, **k):
        return self

    def sort(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return []

    def find_one(self, *a, **k):
        return None

    def delete_one(self, *a, **k):
        return None


class _FakeMsg:
    content = "Pipeline armed."


async def _fake_ainvoke(inputs, config=None):
    await asyncio.sleep(0)  # async to match the real executor.ainvoke it replaces
    return {"messages": [_FakeMsg()], "metadata": {}}


class TestChat:
    async def test_unauthenticated_401(self, client):
        assert (await client.post(f"{BASE}/chat", json={"message": "hi"})).status_code == 401

    async def test_no_company_403(self, client, as_user, auth_user):
        as_user(auth_user("", perms=[]))  # empty company_id
        assert (await client.post(f"{BASE}/chat", json={"message": "hi"})).status_code == 403

    async def test_empty_message_422(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=[]))
        assert (await client.post(f"{BASE}/chat", json={"message": "   "})).status_code == 422

    async def test_happy_path_mocked_agent(self, client, seed_company, as_user, auth_user, monkeypatch):
        as_user(auth_user(seed_company.id, perms=[]))
        monkeypatch.setattr(agents_mod.hr_agent_executor, "ainvoke", _fake_ainvoke)
        r = await client.post(f"{BASE}/chat", json={"message": "Hire a dev"})
        assert r.status_code == 200, r.text
        assert r.json()["response"] == "Pipeline armed."


class TestActionsAndApprovals:
    async def test_actions_401(self, client):
        assert (await client.get(f"{BASE}/actions")).status_code == 401

    async def test_actions_happy(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=[]))
        r = await client.get(f"{BASE}/actions")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_approvals_401(self, client):
        assert (await client.get(f"{BASE}/approvals")).status_code == 401

    async def test_approvals_happy(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=[]))
        r = await client.get(f"{BASE}/approvals")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


class TestPilotSessions:
    async def test_list_401(self, client):
        assert (await client.get(f"{BASE}/pilot/sessions")).status_code == 401

    async def test_list_happy(self, client, seed_company, as_user, auth_user, monkeypatch):
        as_user(auth_user(seed_company.id, perms=[]))
        monkeypatch.setattr(agents_mod, "_pilot_coll", lambda: _FakeColl())
        r = await client.get(f"{BASE}/pilot/sessions")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_save_happy(self, client, seed_company, as_user, auth_user, monkeypatch):
        as_user(auth_user(seed_company.id, perms=[]))
        monkeypatch.setattr(agents_mod, "_pilot_coll", lambda: _FakeColl())
        r = await client.post(
            f"{BASE}/pilot/sessions",
            json={"title": "Chat 1", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "success"

    async def test_delete_happy(self, client, seed_company, as_user, auth_user, monkeypatch):
        as_user(auth_user(seed_company.id, perms=[]))
        monkeypatch.setattr(agents_mod, "_pilot_coll", lambda: _FakeColl())
        r = await client.delete(f"{BASE}/pilot/sessions/{uuid.uuid4()}")
        assert r.status_code == 200


class TestPilotSource:
    async def test_unauthenticated_401(self, client):
        assert (await client.post(f"{BASE}/pilot/source", json={"role": "dev"})).status_code == 401

    async def test_missing_role_422(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=[]))
        # empty role -> 422 from the endpoint's own guard
        r = await client.post(f"{BASE}/pilot/source", json={"role": "  "})
        assert r.status_code == 422

    async def test_happy_path_mocked_search(self, client, seed_company, as_user, auth_user, monkeypatch):
        as_user(auth_user(seed_company.id, perms=[]))

        async def _fake_search(*a, **k):
            await asyncio.sleep(0)  # async to match the real sourcing search it replaces
            return [{"full_name": "Jane", "profile_url": "u", "email": "j@x.com", "platform": "github"}]

        async def _fake_backfill(profiles, limit=None):
            await asyncio.sleep(0)  # async to match the real backfill it replaces
            return profiles

        monkeypatch.setattr("app.router.enterprise.sourcing.search_all_platforms", _fake_search)
        monkeypatch.setattr("app.router.enterprise.sourcing.backfill_contacts", _fake_backfill)
        r = await client.post(f"{BASE}/pilot/source", json={"role": "Python dev", "count": 5})
        assert r.status_code == 200
        assert r.json()["count"] == 1


class TestPilotInvite:
    async def test_unauthenticated_401(self, client):
        r = await client.post(f"{BASE}/pilot/invite", json={"job_id": str(uuid.uuid4()), "candidates": []})
        assert r.status_code == 401

    async def test_invalid_job_id_422(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=[]))
        r = await client.post(f"{BASE}/pilot/invite", json={"job_id": "not-a-uuid", "candidates": []})
        assert r.status_code == 422

    async def test_job_not_found_404(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=[]))
        r = await client.post(f"{BASE}/pilot/invite", json={"job_id": str(uuid.uuid4()), "candidates": []})
        assert r.status_code == 404
