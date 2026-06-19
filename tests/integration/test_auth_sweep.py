"""Auth sweep: EVERY endpoint that depends on authentication must reject an unauthenticated
request (401).

Routes are enumerated from the live app and filtered by INTROSPECTING each route's dependency
tree for `get_current_user` — so public/token endpoints are skipped automatically and new
authenticated endpoints are covered with zero maintenance.
"""

import re

import pytest

from app.core.dependencies import get_current_user
from app.main import app

DUMMY = "00000000-0000-0000-0000-000000000001"
SKIP_PATH_SUBSTR = ("/upload", "/audio", "/documents/", "/ws")  # multipart / streaming


def _requires_auth(route) -> bool:
    """True if the route's dependency tree includes get_current_user (directly or via
    PermissionChecker, which sub-depends on get_current_user)."""
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return False
    stack = [dependant]
    while stack:
        d = stack.pop()
        if getattr(d, "call", None) is get_current_user:
            return True
        stack.extend(getattr(d, "dependencies", []) or [])
    return False


def _collect():
    cases, seen = [], set()
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or []
        if not path.startswith("/api/v1") or not _requires_auth(route):
            continue
        if any(s in path for s in SKIP_PATH_SUBSTR):
            continue
        concrete = re.sub(r"\{[^}]+\}", DUMMY, path)
        for m in sorted(methods):
            if m in ("GET", "POST", "PUT", "PATCH", "DELETE") and (m, concrete) not in seen:
                seen.add((m, concrete))
                cases.append((m, concrete))
    return cases


PROTECTED_ENDPOINTS = _collect()


@pytest.mark.parametrize("method,path", PROTECTED_ENDPOINTS)
async def test_endpoint_requires_authentication(client, method, path):
    r = await client.request(method, path, json={})
    assert r.status_code == 401, f"{method} {path} returned {r.status_code}, expected 401"
