"""RBAC sweep: every permission-gated endpoint must reject a logged-in user who lacks the
required permission (403).

Routes carrying a `PermissionChecker` are discovered by introspection; a zero-permission user
is then denied on each.
"""

import re

import pytest

from app.core.dependencies import PermissionChecker
from app.main import app

DUMMY = "00000000-0000-0000-0000-000000000001"
SKIP_PATH_SUBSTR = ("/upload", "/audio", "/documents/", "/ws")


def _has_permission_checker(route) -> bool:
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return False
    stack = [dependant]
    while stack:
        d = stack.pop()
        if isinstance(getattr(d, "call", None), PermissionChecker):
            return True
        stack.extend(getattr(d, "dependencies", []) or [])
    return False


def _collect():
    cases, seen = [], set()
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or []
        if not path.startswith("/api/v1") or not _has_permission_checker(route):
            continue
        if any(s in path for s in SKIP_PATH_SUBSTR):
            continue
        concrete = re.sub(r"\{[^}]+\}", DUMMY, path)
        for m in sorted(methods):
            if m in ("GET", "POST", "PUT", "PATCH", "DELETE") and (m, concrete) not in seen:
                seen.add((m, concrete))
                cases.append((m, concrete))
    return cases


GATED_ENDPOINTS = _collect()


@pytest.mark.parametrize("method,path", GATED_ENDPOINTS)
async def test_endpoint_enforces_permission(client, seed_company, as_user, auth_user, method, path):
    as_user(auth_user(seed_company.id, perms=[]))  # authenticated but no permissions
    r = await client.request(method, path, json={})
    assert r.status_code == 403, f"{method} {path} returned {r.status_code}, expected 403"
