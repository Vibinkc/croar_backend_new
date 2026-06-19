"""DB-backed tests for the Croar Pilot agent tools (app/agents/tools.py).

These drive the real tool implementations against the SQLite test DB (no LLM/network), exercising
create / list / update / delete job tools and their edge cases — invalid ids, tenant scoping,
no-op updates, and the "delete preserves hired candidates" rule.
"""

import uuid

from app.agents.tools import (
    create_job_requisition,
    delete_job,
    list_jobs,
    score_candidate_application,
    update_job,
)
from app.models.enterprise.candidate import Candidate, CandidateApplication
from app.models.enterprise.job import JobRequirement


def _cfg(session, company_id):
    return {"configurable": {"session": session, "company_id": str(company_id)}}


async def _make_job(session, company_id, title="Backend Engineer", status_id=2):
    job = JobRequirement(title=title, description="Build APIs", company_id=company_id, status_id=status_id)
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def _make_application(session, company_id, job_id, status_id):
    cand = Candidate(company_id=company_id) if hasattr(Candidate, "company_id") else Candidate()
    session.add(cand)
    await session.commit()
    await session.refresh(cand)
    app_row = CandidateApplication(candidate_id=cand.id, job_requirement_id=job_id, status_id=status_id)
    session.add(app_row)
    await session.commit()
    await session.refresh(app_row)
    return app_row


class TestCreateJobRequisition:
    async def test_creates_live_job(self, db_session, seed_company):
        res = await create_job_requisition.ainvoke(
            {
                "role_title": "Platform Engineer",
                "jd_content": "Own the platform.",
                "target_company_id": str(seed_company.id),
                "skills": ["Go", "K8s"],
            },
            config=_cfg(db_session, seed_company.id),
        )
        assert res["status"] == "success"
        assert res["active"] is True
        assert res["job_id"]

    async def test_blank_title_is_rejected(self, db_session, seed_company):
        res = await create_job_requisition.ainvoke(
            {"role_title": "   ", "jd_content": "x", "target_company_id": str(seed_company.id)},
            config=_cfg(db_session, seed_company.id),
        )
        assert res["status"] == "error"

    async def test_default_jd_when_blank(self, db_session, seed_company):
        res = await create_job_requisition.ainvoke(
            {"role_title": "Data Scientist", "jd_content": "", "target_company_id": str(seed_company.id)},
            config=_cfg(db_session, seed_company.id),
        )
        assert res["status"] == "success"
        job = await db_session.get(JobRequirement, uuid.UUID(res["job_id"]))
        assert "Data Scientist" in job.description


class TestListJobs:
    async def test_empty(self, db_session, seed_company):
        res = await list_jobs.ainvoke({}, config=_cfg(db_session, seed_company.id))
        assert res["status"] == "success"
        assert res["count"] == 0
        assert res["jobs"] == []

    async def test_lists_created_job(self, db_session, seed_company):
        await _make_job(db_session, seed_company.id, title="QA Lead")
        res = await list_jobs.ainvoke({}, config=_cfg(db_session, seed_company.id))
        assert res["count"] == 1
        assert res["jobs"][0]["title"] == "QA Lead"
        assert res["jobs"][0]["active"] is True

    async def test_excludes_other_company(self, db_session, seed_company):
        other = uuid.uuid4()
        await _make_job(db_session, other, title="Not Mine")
        res = await list_jobs.ainvoke({}, config=_cfg(db_session, seed_company.id))
        assert res["count"] == 0

    async def test_excludes_soft_deleted(self, db_session, seed_company):
        from datetime import datetime

        job = await _make_job(db_session, seed_company.id)
        job.deleted_at = datetime.now()
        await db_session.commit()
        res = await list_jobs.ainvoke({}, config=_cfg(db_session, seed_company.id))
        assert res["count"] == 0


class TestUpdateJob:
    async def test_invalid_id(self, db_session, seed_company):
        res = await update_job.ainvoke(
            {"job_id": "not-a-uuid", "title": "X"}, config=_cfg(db_session, seed_company.id)
        )
        assert res["status"] == "error"

    async def test_missing_job(self, db_session, seed_company):
        res = await update_job.ainvoke(
            {"job_id": str(uuid.uuid4()), "title": "X"}, config=_cfg(db_session, seed_company.id)
        )
        assert res["status"] == "error"

    async def test_no_fields_is_no_change(self, db_session, seed_company):
        job = await _make_job(db_session, seed_company.id)
        res = await update_job.ainvoke({"job_id": str(job.id)}, config=_cfg(db_session, seed_company.id))
        assert res["status"] == "no_change"

    async def test_updates_title_and_status(self, db_session, seed_company):
        job = await _make_job(db_session, seed_company.id)
        res = await update_job.ainvoke(
            {"job_id": str(job.id), "title": "Senior Backend", "is_active": False},
            config=_cfg(db_session, seed_company.id),
        )
        assert res["status"] == "success"
        assert "title" in res["updated"] and "status" in res["updated"]
        await db_session.refresh(job)
        assert job.title == "Senior Backend"
        assert job.status_id == 1  # moved to Draft

    async def test_cannot_update_other_company_job(self, db_session, seed_company):
        other_job = await _make_job(db_session, uuid.uuid4(), title="Theirs")
        res = await update_job.ainvoke(
            {"job_id": str(other_job.id), "title": "Hijacked"}, config=_cfg(db_session, seed_company.id)
        )
        assert res["status"] == "error"


class TestDeleteJob:
    async def test_invalid_id(self, db_session, seed_company):
        res = await delete_job.ainvoke({"job_id": "nope"}, config=_cfg(db_session, seed_company.id))
        assert res["status"] == "error"

    async def test_missing_job(self, db_session, seed_company):
        res = await delete_job.ainvoke(
            {"job_id": str(uuid.uuid4())}, config=_cfg(db_session, seed_company.id)
        )
        assert res["status"] == "error"

    async def test_soft_deletes_job(self, db_session, seed_company):
        job = await _make_job(db_session, seed_company.id)
        res = await delete_job.ainvoke({"job_id": str(job.id)}, config=_cfg(db_session, seed_company.id))
        assert res["status"] == "success"
        await db_session.refresh(job)
        assert job.deleted_at is not None

    async def test_preserves_hired_removes_others(self, db_session, seed_company):
        job = await _make_job(db_session, seed_company.id)
        hired = await _make_application(db_session, seed_company.id, job.id, status_id=5)
        rejected = await _make_application(db_session, seed_company.id, job.id, status_id=3)

        res = await delete_job.ainvoke({"job_id": str(job.id)}, config=_cfg(db_session, seed_company.id))
        assert res["status"] == "success"

        await db_session.refresh(hired)
        await db_session.refresh(rejected)
        assert hired.deleted_at is None  # hired candidate preserved
        assert rejected.deleted_at is not None  # non-hired soft-deleted


class TestScoreApplication:
    async def test_invalid_application_id(self, db_session, seed_company):
        res = await score_candidate_application.ainvoke(
            {"target_application_id": "garbage"}, config=_cfg(db_session, seed_company.id)
        )
        assert res["status"] == "error"
