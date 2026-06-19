"""Validation sweep: a permitted user sending an empty body to a write endpoint that has
required fields gets 422 (request-body validation).

The route's own required permission is read off its PermissionChecker and granted, so the
request reaches body validation rather than stopping at 401/403. Endpoints whose body is
entirely optional are discovered (they return non-422) and excluded by `_has_required_body`.
"""

import re

import pytest

from app.core.dependencies import PermissionChecker
from app.main import app

DUMMY = "00000000-0000-0000-0000-000000000001"
SKIP_PATH_SUBSTR = (
    "/upload",
    "/audio",
    "/documents/",
    "/ws",
    "/portal",
    "/sourcing/chat",
    "/super-admin/tenants/",  # sub-resource creates look up the (absent) tenant before body validation
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


def _has_required_body(route) -> bool:
    """True if the route declares a JSON body with at least one required field."""
    body_fields = getattr(getattr(route, "dependant", None), "body_params", None) or []
    for field in body_fields:
        # A pydantic model body — inspect its required fields.
        model = getattr(field, "type_", None)
        model_fields = getattr(model, "model_fields", None)
        if model_fields:
            if any(f.is_required() for f in model_fields.values()):
                return True
        elif getattr(field, "required", False):
            return True
    return False


def _collect():
    cases, seen = [], set()
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or []
        if not path.startswith("/api/v1") or any(s in path for s in SKIP_PATH_SUBSTR):
            continue
        pc = _permission_checker(route)
        if pc is None or not _has_required_body(route):
            continue
        concrete = re.sub(r"\{[^}]+\}", DUMMY, path)
        for m in sorted(methods):
            if m in ("POST", "PUT", "PATCH") and (m, concrete) not in seen:
                seen.add((m, concrete))
                cases.append((m, concrete, pc.module, pc.action))
    return cases


WRITE_ENDPOINTS = _collect()


@pytest.mark.parametrize("method,path,module,action", WRITE_ENDPOINTS)
async def test_empty_body_is_rejected(client, seed_company, as_user, auth_user, method, path, module, action):
    as_user(auth_user(seed_company.id, perms=[(module, action)]))
    r = await client.request(method, path, json={})
    # 422 = pydantic body validation; some endpoints hand-validate and return 400. Either is a
    # correct rejection of a missing/empty body.
    assert r.status_code in (400, 422), f"{method} {path} returned {r.status_code}, expected 400/422"
