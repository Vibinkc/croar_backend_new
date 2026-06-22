"""Happy-path list sweep: a permitted user hitting a no-parameter collection GET endpoint on an
empty database gets a clean 200 (not 500). Catches serialization/query regressions across every
listing endpoint with zero per-endpoint maintenance.
"""

import pytest

from app.core.dependencies import PermissionChecker
from app.main import app

SKIP_PATH_SUBSTR = (
    "/upload",
    "/audio",
    "/documents/",
    "/ws",
    "/portal",
    "/sourcing",
    "/settings/payslip/document/mapping",  # returns 404 until a mapping is configured (not a list)
)


def _has_required_query_param(route) -> bool:
    """True if the route declares a required query parameter (a bare GET would 422, not 200)."""
    for field in getattr(getattr(route, "dependant", None), "query_params", None) or []:
        if getattr(field, "required", False):
            return True
    return False


def _permission_checker(route):
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return None
    stack = [dependant]
    while stack:
        d = stack.pop()
        call = getattr(d, "call", None)
        if isinstance(call, PermissionChecker):
            return call
        stack.extend(getattr(d, "dependencies", []) or [])
    return None


def _collect():
    cases, seen = [], set()
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or []
        # No-parameter GET collections only — nothing to 404 on, so a permitted user should get 200.
        if not path.startswith("/api/v1") or "{" in path or "GET" not in methods:
            continue
        if any(s in path for s in SKIP_PATH_SUBSTR):
            continue
        if _has_required_query_param(route):  # e.g. /reports/salary-register needs ?cycle_id=
            continue
        pc = _permission_checker(route)
        if pc is None or path in seen:
            continue
        seen.add(path)
        cases.append((path, pc.module, pc.action))
    return cases


LIST_ENDPOINTS = _collect()


@pytest.mark.parametrize("path,module,action", LIST_ENDPOINTS)
async def test_collection_returns_200(client, seed_company, as_user, auth_user, path, module, action):
    as_user(auth_user(seed_company.id, perms=[(module, action)]))
    r = await client.get(path)
    assert r.status_code == 200, f"GET {path} returned {r.status_code}, expected 200"
