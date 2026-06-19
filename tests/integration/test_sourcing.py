"""Integration tests for the Sourcing router (/api/v1/enterprise/sourcing).

The live search hits the network; here we cover the auth surface (every endpoint requires a
logged-in user) without making real HTTP calls.
"""

import pytest

BASE = "/api/v1/enterprise/sourcing"
# Fixed (not uuid4()) so parametrization is identical across xdist workers — a random id at
# collection time makes each worker collect a different test id and xdist aborts.
_FIXED_ID = "00000000-0000-0000-0000-000000000009"


# The chat/shortlist endpoints require a logged-in user. (/search is intentionally public,
# and it hits the network, so it's excluded here.)
@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", f"{BASE}/chat/jobs"),
        ("GET", f"{BASE}/chat/shortlisted"),
        ("POST", f"{BASE}/chat/sessions"),
        ("POST", f"{BASE}/chat/shortlist"),
        ("DELETE", f"{BASE}/chat/shortlisted/{_FIXED_ID}"),
    ],
)
async def test_endpoints_require_auth(client, method, path):
    r = await client.request(method, path, json={})
    assert r.status_code == 401
