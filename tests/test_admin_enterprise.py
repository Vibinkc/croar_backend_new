"""Tests for the Admin (enterprise) routes: app/router/enterprise/* and app/router/agents.py.

Covers authentication enforcement, RBAC, the agents-router security fix, and the
shortlist null-profile edge-case fix.
"""

import pytest

from app.models.shared.constants import ModuleScope, PermissionAction

# (method, path) — protected enterprise/agent endpoints that must require auth.
PROTECTED_ENDPOINTS = [
    ("GET", "/api/v1/enterprise/employees/"),
    ("GET", "/api/v1/enterprise/employees/departments"),
    ("GET", "/api/v1/enterprise/sourcing/chat/sessions"),
    ("GET", "/api/v1/enterprise/sourcing/chat/shortlisted"),
    ("GET", "/api/v1/enterprise/sourcing/chat/jobs"),
    # Security fix: the agent audit-log endpoints used to be fully unauthenticated.
    ("GET", "/api/v1/agents/actions"),
    ("GET", "/api/v1/agents/approvals"),
]


class TestEnterpriseAuthentication:
    @pytest.mark.parametrize("method,path", PROTECTED_ENDPOINTS)
    def test_requires_authentication(self, client, method, path):
        resp = client.request(method, path)
        assert resp.status_code == 401, f"{method} {path} returned {resp.status_code}, expected 401"


class TestEnterpriseAuthorization:
    def test_user_without_employees_permission_forbidden(self, client, auth_as, make_user):
        auth_as(make_user(perms=[(ModuleScope.candidates, PermissionAction.read)]))
        assert client.get("/api/v1/enterprise/employees/").status_code == 403

    def test_user_with_employees_permission_passes_auth(self, client, auth_as, make_user):
        # Correct permission -> gets past the auth layer (not 401/403).
        auth_as(make_user(perms=[(ModuleScope.employees, PermissionAction.read)]))
        assert client.get("/api/v1/enterprise/employees/").status_code not in (401, 403)


class TestAgentsRouterSecurityFix:
    """The agents audit/approval endpoints now require authentication."""

    def test_actions_requires_auth(self, client):
        assert client.get("/api/v1/agents/actions").status_code == 401

    def test_approvals_requires_auth(self, client):
        assert client.get("/api/v1/agents/approvals").status_code == 401


class TestShortlistEdgeCase:
    """Edge-case fix: POST /sourcing/chat/shortlist must 400 when 'profile' is missing,
    instead of 500-ing on a None.get()."""

    def test_missing_profile_returns_400(self, client, auth_as, make_user):
        auth_as(make_user(perms=[(ModuleScope.candidates, PermissionAction.read)]))
        resp = client.post(
            "/api/v1/enterprise/sourcing/chat/shortlist",
            json={"job_id": "j1", "job_title": "Engineer"},  # no 'profile'
        )
        assert resp.status_code == 400

    def test_null_profile_returns_400(self, client, auth_as, make_user):
        auth_as(make_user(perms=[(ModuleScope.candidates, PermissionAction.read)]))
        resp = client.post(
            "/api/v1/enterprise/sourcing/chat/shortlist", json={"job_id": "j1", "profile": None}
        )
        assert resp.status_code == 400
