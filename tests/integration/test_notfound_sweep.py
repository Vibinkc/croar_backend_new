"""Not-found sweep: a permitted user requesting a non-existent resource by id gets 404.

For each id-param GET/DELETE endpoint we read its required permission off the route's
PermissionChecker, grant exactly that, then request a random id.
"""

import re

import pytest

from app.core.dependencies import PermissionChecker
from app.main import app

DUMMY = "00000000-0000-0000-0000-000000000001"
# Mongo-backed (/sourcing chat) and super-admin/system have non-SQLite id handling; sub-resource
# collection endpoints (path doesn't END in the id param) legitimately return 200-empty, not 404.
SKIP_PATH_SUBSTR = (
    "/upload",
    "/audio",
    "/documents/",
    "/ws",
    "/portal",
    "/sourcing/chat",
    "/super-admin/system",
    "/simulations/scenarios",  # idempotent bulk delete returns 200 even when absent
    "/timesheets/cycles",  # returns 200 (resolves/creates the cycle) rather than 404 on unknown id
)


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
        # Only true "resource-by-id" endpoints: the path must END in a path param, so
        # `/jobs/{id}` is in but `/projects/{id}/tasks` (a sub-collection) is out.
        if not path.startswith("/api/v1") or not path.rstrip("/").endswith("}"):
            continue
        if any(s in path for s in SKIP_PATH_SUBSTR):
            continue
        pc = _permission_checker(route)
        if pc is None:
            continue
        concrete = re.sub(r"\{[^}]+\}", DUMMY, path)
        for m in sorted(methods):
            if m in ("GET", "DELETE") and (m, concrete) not in seen:
                seen.add((m, concrete))
                cases.append((m, concrete, pc.module, pc.action))
    return cases


NOTFOUND_ENDPOINTS = _collect()


@pytest.mark.parametrize("method,path,module,action", NOTFOUND_ENDPOINTS)
async def test_unknown_resource_404(client, seed_company, as_user, auth_user, method, path, module, action):
    as_user(auth_user(seed_company.id, perms=[(module, action)]))
    r = await client.request(method, path)
    assert r.status_code == 404, f"{method} {path} returned {r.status_code}, expected 404"
