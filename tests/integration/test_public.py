"""Integration tests for PUBLIC (no-login) endpoints: public job listing + onboarding link."""

import uuid

PUBLIC_JOBS = "/api/v1/enterprise/public/jobs"
PUBLIC_ONB = "/api/v1/enterprise/public/onboarding"


class TestPublicJobs:
    async def test_list_is_public(self, client):
        r = await client.get(f"{PUBLIC_JOBS}/list")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_get_unknown_job_404(self, client):
        assert (await client.get(f"{PUBLIC_JOBS}/{uuid.uuid4()}")).status_code == 404

    async def test_get_published_job(self, client, db_session, seed_company):
        from app.models.enterprise.job import JobRequirement

        job = JobRequirement(
            title="Public Role", description="Apply here", company_id=seed_company.id, status_id=2
        )
        db_session.add(job)
        await db_session.commit()
        await db_session.refresh(job)
        r = await client.get(f"{PUBLIC_JOBS}/{job.id}")
        assert r.status_code == 200
        assert r.json()["job"]["title"] == "Public Role"


class TestPublicOnboarding:
    async def test_bad_token_404(self, client):
        assert (await client.get(f"{PUBLIC_ONB}/{uuid.uuid4().hex}")).status_code == 404
