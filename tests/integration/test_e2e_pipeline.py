"""End-to-end tests for the Croar Pilot pipeline build + the public job flow.

The headline test runs the real `build_hiring_pipeline` agent tool against the SQLite DB
(only the LLM question generators are mocked) and asserts the ENTIRE pipeline is created:
live job + all four automations + three role-specific templates.
"""

import asyncio

from sqlalchemy import func, select

from app.agents.tools import build_hiring_pipeline
from app.models.enterprise.assessment import AssessmentAutomation, AssessmentTemplate
from app.models.enterprise.communication import MailAutomation
from app.models.enterprise.interview import Interview, InterviewAutomation
from app.models.enterprise.job import JobRequirement
from app.models.enterprise.onboarding import OnboardingAutomation, OnboardingTemplate


async def _count(session, model, company_id):
    res = await session.execute(select(func.count()).select_from(model).where(model.company_id == company_id))
    return res.scalar_one()


class TestBuildHiringPipeline:
    async def test_creates_job_automations_and_templates(self, db_session, seed_company, monkeypatch):
        async def _no_questions(*a, **k):
            await asyncio.sleep(0)  # async to match the real question generators it replaces
            return []

        monkeypatch.setattr("app.services.enterprise.ai_service.generate_assessment_questions", _no_questions)
        monkeypatch.setattr(
            "app.services.enterprise.ai_service.generate_interview_questions_service", _no_questions
        )

        config = {"configurable": {"session": db_session, "company_id": str(seed_company.id)}}
        result = await build_hiring_pipeline.ainvoke(
            {
                "role_title": "Backend Engineer",
                "jd_content": "Build APIs.",
                "skills": ["Python", "FastAPI"],
                "assessment_type": "CODING",
                "interview_type": "AI",
            },
            config=config,
        )

        assert result["status"] == "success", result
        assert result["ui"] == "pipeline_built"
        assert result["job_id"]

        cid = seed_company.id
        # One live job...
        assert await _count(db_session, JobRequirement, cid) == 1
        job = (await db_session.execute(select(JobRequirement))).scalar_one()
        assert job.status_id == 2  # ACTIVE / LIVE
        # ...two mail automations (screening + offer)...
        assert await _count(db_session, MailAutomation, cid) == 2
        # ...one each of assessment / interview / onboarding automation...
        assert await _count(db_session, AssessmentAutomation, cid) == 1
        assert await _count(db_session, InterviewAutomation, cid) == 1
        assert await _count(db_session, OnboardingAutomation, cid) == 1
        # ...and the three role-specific templates.
        assert await _count(db_session, AssessmentTemplate, cid) == 1
        assert await _count(db_session, Interview, cid) == 1
        assert await _count(db_session, OnboardingTemplate, cid) == 1

    async def test_interview_automation_has_generated_time_slots(self, db_session, seed_company, monkeypatch):
        async def _no_questions(*a, **k):
            await asyncio.sleep(0)  # async to match the real question generators it replaces
            return []

        monkeypatch.setattr("app.services.enterprise.ai_service.generate_assessment_questions", _no_questions)
        monkeypatch.setattr(
            "app.services.enterprise.ai_service.generate_interview_questions_service", _no_questions
        )
        config = {"configurable": {"session": db_session, "company_id": str(seed_company.id)}}
        await build_hiring_pipeline.ainvoke(
            {"role_title": "Dev", "jd_content": "x", "interview_slots_per_day": 5}, config=config
        )
        auto = (await db_session.execute(select(InterviewAutomation))).scalar_one()
        # The interview automation should have its time slots populated (the "0 slots" fix).
        assert auto.time_slots and len(auto.time_slots) >= 1


class TestPublicJobFlow:
    async def test_created_job_is_publicly_visible(self, client, seed_company, as_user, auth_user):
        from app.models.shared.constants import ModuleScope, PermissionAction

        # Recruiter creates a live job...
        as_user(auth_user(seed_company.id, perms=[(ModuleScope.jobs, PermissionAction.create)]))
        created = (
            await client.post(
                "/api/v1/enterprise/jobs/", json={"title": "Public E2E Role", "description": "Join us"}
            )
        ).json()

        # ...and it's reachable on the public careers page (that endpoint ignores auth).
        r = await client.get(f"/api/v1/enterprise/public/jobs/{created['id']}")
        assert r.status_code == 200
        assert r.json()["job"]["title"] == "Public E2E Role"
