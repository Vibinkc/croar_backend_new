"""A small, narrated demo: drive the real Jobs API end-to-end with real data.

Run it with:  pytest tests/integration/test_demo_realdata.py -v -s
Every step prints what actually happened so you can see the real request/response + DB row.
"""

from sqlalchemy import select

from app.models.enterprise.job import JobRequirement
from app.models.shared.constants import ModuleScope, PermissionAction


async def test_demo_create_read_update_list_a_real_job(client, db_session, seed_company, as_user, auth_user):
    # --- 1. Authenticate as a real recruiter who may create + read + update jobs ---
    as_user(
        auth_user(
            seed_company.id,
            perms=[
                (ModuleScope.jobs, PermissionAction.create),
                (ModuleScope.jobs, PermissionAction.read),
                (ModuleScope.jobs, PermissionAction.update),
            ],
        )
    )
    print(f"\n[1] Acting as recruiter at company {seed_company.name} ({seed_company.id})")

    # --- 2. CREATE a real job through the API (real request -> handler -> DB write) ---
    payload = {
        "title": "Senior Backend Engineer",
        "description": "Build and scale the Croar hiring platform APIs (FastAPI, Postgres, Celery).",
    }
    r = await client.post("/api/v1/enterprise/jobs/", json=payload)
    print(f"[2] POST /jobs/  -> {r.status_code}")
    assert r.status_code in (200, 201), r.text
    created = r.json()
    job_id = created["id"]
    print(f"    created job id = {job_id}, title = {created['title']!r}")

    # --- 3. READ it back through the API (real DB read) ---
    r = await client.get(f"/api/v1/enterprise/jobs/{job_id}")
    print(f"[3] GET  /jobs/{{id}} -> {r.status_code}")
    assert r.status_code == 200
    fetched = r.json()
    assert fetched["title"] == payload["title"]
    assert fetched["description"] == payload["description"]
    print(f"    read-back matches: title={fetched['title']!r}")

    # --- 4. Verify the row REALLY exists in the database (not a mock) ---
    row = (
        await db_session.execute(
            select(JobRequirement).where(JobRequirement.title == "Senior Backend Engineer")
        )
    ).scalar_one()
    assert str(row.id) == str(job_id)
    assert row.company_id == seed_company.id  # correctly scoped to this tenant
    print(f"[4] DB row confirmed: id={row.id}, company_id={row.company_id}, status_id={row.status_id}")

    # --- 5. UPDATE the title through the API, then confirm it persisted ---
    r = await client.patch(f"/api/v1/enterprise/jobs/{job_id}", json={"title": "Staff Backend Engineer"})
    print(f"[5] PATCH /jobs/{{id}} -> {r.status_code}")
    assert r.status_code == 200
    await db_session.refresh(row)
    assert row.title == "Staff Backend Engineer"
    print(f"    DB now shows updated title = {row.title!r}")

    # --- 6. LIST jobs and confirm ours shows up ---
    r = await client.get("/api/v1/enterprise/jobs/")
    listed = r.json()
    titles = [j["title"] for j in listed]
    print(f"[6] GET  /jobs/  -> {r.status_code}, {len(listed)} job(s): {titles}")
    assert "Staff Backend Engineer" in titles
    print("\n[OK] Real job created, read, verified in DB, updated, and listed — all through the live API.\n")
