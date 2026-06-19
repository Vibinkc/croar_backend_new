"""Tests for the Super-Admin (platform) routes: app/router/platform/*.

Covers authentication enforcement, permission (RBAC) enforcement, input validation,
and the public-settings allowlist security fix.
"""

import uuid

import pytest

from app.models.shared.constants import ModuleScope, PermissionAction

UUID = "00000000-0000-0000-0000-000000000000"
DUMMY_PASSWORD = uuid.uuid4().hex  # generated test value, not a hardcoded credential

# (method, path, json_body) — body kept minimal/valid so AUTH is the gate, not 422.
PROTECTED_ENDPOINTS = [
    ("GET", "/api/v1/super-admin/stats", None),
    ("GET", "/api/v1/super-admin/tenants", None),
    ("POST", "/api/v1/super-admin/tenants", {}),
    ("GET", f"/api/v1/super-admin/tenants/{UUID}", None),
    ("PUT", f"/api/v1/super-admin/tenants/{UUID}", {}),
    ("DELETE", f"/api/v1/super-admin/tenants/{UUID}", None),
    ("GET", f"/api/v1/super-admin/tenants/{UUID}/divisions", None),
    ("GET", f"/api/v1/super-admin/tenants/{UUID}/admins", None),
    ("POST", f"/api/v1/super-admin/tenants/{UUID}/admins", {}),
    ("GET", f"/api/v1/super-admin/tenants/{UUID}/users", None),
    ("POST", f"/api/v1/super-admin/tenants/{UUID}/users", {}),
    ("DELETE", f"/api/v1/super-admin/tenants/{UUID}/users/{UUID}", None),
    ("GET", "/api/v1/super-admin/roles", None),
    ("GET", f"/api/v1/super-admin/roles/{UUID}", None),
    ("GET", "/api/v1/super-admin/permissions", None),
    ("GET", "/api/v1/super-admin/system/settings", None),
    ("PATCH", "/api/v1/super-admin/system/settings/some_key", {"value": True}),
    ("GET", "/api/v1/super-admin/system/audit-logs", None),
    ("GET", "/api/v1/super-admin/system/users", None),
]


class TestSuperAdminAuthentication:
    """Every protected platform endpoint must reject anonymous callers with 401."""

    @pytest.mark.parametrize("method,path,body", PROTECTED_ENDPOINTS)
    def test_requires_authentication(self, client, method, path, body):
        resp = client.request(method, path, json=body)
        assert resp.status_code == 401, f"{method} {path} returned {resp.status_code}, expected 401"


class TestSuperAdminAuthorization:
    """A logged-in user without the platform permission must get 403."""

    def test_non_platform_user_forbidden_listing_tenants(self, client, auth_as, make_user):
        auth_as(make_user(perms=[(ModuleScope.candidates, PermissionAction.read)]))
        assert client.get("/api/v1/super-admin/tenants").status_code == 403

    def test_non_platform_user_forbidden_listing_roles(self, client, auth_as, make_user):
        auth_as(make_user(perms=[(ModuleScope.employees, PermissionAction.read)]))
        assert client.get("/api/v1/super-admin/roles").status_code == 403

    def test_platform_admin_passes_auth_layer(self, client, auth_as, make_user):
        # With the right permission the request gets PAST auth (so NOT 401/403).
        auth_as(make_user(perms=[(ModuleScope.platform, PermissionAction.moderate)]))
        resp = client.post("/api/v1/super-admin/tenants", json={})  # then fails validation
        assert resp.status_code not in (401, 403)


class TestCreateTenantValidation:
    """Input validation on tenant creation (runs before any DB access)."""

    @pytest.fixture(autouse=True)
    def _as_admin(self, auth_as, make_user):
        auth_as(make_user(perms=[(ModuleScope.platform, PermissionAction.moderate)]))

    def test_missing_admin_credentials_returns_400(self, client):
        resp = client.post("/api/v1/super-admin/tenants", json={"name": "Acme"})
        assert resp.status_code == 400

    def test_missing_org_name_returns_400(self, client):
        resp = client.post(
            "/api/v1/super-admin/tenants", json={"admin_email": "a@b.com", "admin_password": DUMMY_PASSWORD}
        )
        assert resp.status_code == 400


class TestPublicSettingsAllowlist:
    """Security fix: the unauthenticated per-key settings endpoint is allowlisted."""

    def test_arbitrary_key_is_blocked(self, client):
        # A non-allowlisted key must NOT be publicly readable.
        resp = client.get("/api/v1/super-admin/system/settings/internal_secret_flag")
        assert resp.status_code == 404

    @pytest.mark.parametrize("key", ["signup_enabled", "google_sso_enabled", "microsoft_sso_enabled"])
    def test_allowlisted_keys_are_public(self, client, fake_db, key):
        # The login/signup pages need these pre-auth -> the allowlist must permit them
        # (reachable, returns 200 — fake_db keeps it off Postgres for determinism).
        resp = client.get(f"/api/v1/super-admin/system/settings/{key}")
        assert resp.status_code == 200
        assert resp.json().get("key") == key
