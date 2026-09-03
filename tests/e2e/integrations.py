"""Exercise the integration marketplace on a fresh account.

The point is the consent gate and the free-tier verification: connecting must refuse without
agreement, must refuse a key the provider rejects, and must record who agreed and when.
"""

import uuid

import httpx

BASE = "http://localhost:8000/api/v1"
c = httpx.Client(base_url=BASE, timeout=90)

stamp = uuid.uuid4().hex[:8]
email, pw = f"intg.{stamp}@croar-e2e.test", "QaWalk!2026x"
c.post(
    "/auth/signup",
    json={
        "email": email,
        "password": pw,
        "first_name": "I",
        "last_name": "T",
        "company_name": f"Intg {stamp}",
    },
)
H = {
    "Authorization": f"Bearer {c.post('/auth/token', data={'username': email, 'password': pw}).json()['access_token']}"
}

cat = c.get("/enterprise/integrations/catalog", headers=H).json()
items = cat["integrations"]
print(f"catalogue: {len(items)} integrations")
cats = {}
for i in items:
    cats.setdefault(i["category"], []).append(i["name"])
for k, v in cats.items():
    print(f"  {k}: {len(v)} — {', '.join(v)}")
print("all have what_it_does:", all(i.get("what_it_does") for i in items))
print("all have requires_consent:", all("requires_consent" in i for i in items))

# 1. Connecting without agreeing must be refused.
r = c.post(
    "/enterprise/integrations/connections",
    headers=H,
    json={
        "integration": "codility",
        "credentials": {"invite_url": "https://app.codility.com/test/abc"},
        "agreed_to_terms": False,
    },
)
print(f"\nno consent      -> {r.status_code} {r.json().get('detail', '')[:70]}")

# 2. With consent it connects, and the agreement is recorded.
r = c.post(
    "/enterprise/integrations/connections",
    headers=H,
    json={
        "integration": "codility",
        "credentials": {"invite_url": "https://app.codility.com/test/abc"},
        "agreed_to_terms": True,
    },
)
print(f"with consent    -> {r.status_code}")
conn = r.json().get("connection", {}) if r.status_code < 300 else {}
print(f"  agreed_by={conn.get('agreed_by')} agreed_at={str(conn.get('agreed_at'))[:19]}")

# 3. A free-tier provider is checked against its own API, so a bad key is caught on connect.
r = c.post(
    "/enterprise/integrations/connections",
    headers=H,
    json={
        "integration": "jotform",
        "credentials": {"api_key": "definitely-not-a-real-key"},
        "agreed_to_terms": True,
    },
)
print(f"bad jotform key -> {r.status_code} {str(r.json().get('detail', ''))[:70]}")

# 4. The connection shows up, and disconnect removes it.
lst = c.get("/enterprise/integrations/connections", headers=H).json()
print(f"\nconnections: {[x['integration'] for x in lst['connections']]}")
print("disconnect      ->", c.delete("/enterprise/integrations/connections/codility", headers=H).status_code)
print(
    "after           :",
    [
        x["integration"]
        for x in c.get("/enterprise/integrations/connections", headers=H).json()["connections"]
    ],
)
