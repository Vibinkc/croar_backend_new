"""DB-backed unit tests for the service layer (against the SQLite test DB).

Covers the service functions whose logic touches the database: interview slot-finding,
employee-id generation (numeric, not lexicographic), and the EnterpriseService queries.
Email/Google/LLM side effects are not invoked by these code paths.
"""

import uuid
from datetime import date, datetime, timedelta

from app.models.enterprise.employee import Employee
from app.models.enterprise.interview import InterviewAutomation
from app.models.enterprise.job import JobRequirement
from app.services.enterprise.employee_service import EmployeeService
from app.services.enterprise.interview_service import find_available_slot
from app.services.enterprise_service import EnterpriseService


def _automation(**overrides):
    auto = InterviewAutomation(job_requirement_id=uuid.uuid4(), criteria="")
    auto.id = uuid.uuid4()
    auto.start_date = None
    auto.end_date = None
    auto.start_time = "09:00"
    auto.end_time = "17:00"
    auto.duration = 30
    auto.daily_limit = 1
    auto.time_slots = None
    for k, v in overrides.items():
        setattr(auto, k, v)
    return auto


class TestFindAvailableSlot:
    async def test_returns_future_weekday_at_start_time(self, db_session):
        slot = await find_available_slot(db_session, _automation())
        assert isinstance(slot, datetime)
        assert slot.date() >= date.today() + timedelta(days=1)
        assert slot.weekday() < 5  # never a weekend
        assert (slot.hour, slot.minute) == (9, 0)

    async def test_respects_custom_time_slots(self, db_session):
        slot = await find_available_slot(db_session, _automation(time_slots=["14:00", "15:30"]))
        assert (slot.hour, slot.minute) == (14, 0)

    async def test_respects_start_date(self, db_session):
        future = date.today() + timedelta(days=20)
        slot = await find_available_slot(db_session, _automation(start_date=future))
        assert slot.date() >= future


class TestGenerateEmployeeId:
    async def _add_employee(self, db_session, company_id, emp_id):
        db_session.add(
            Employee(
                employee_id=emp_id,
                first_name="A",
                last_name="B",
                email=f"{emp_id}@x.com",
                company_id=company_id,
            )
        )
        await db_session.commit()

    async def test_first_id_is_1001(self, db_session, seed_company):
        assert await EmployeeService.generate_employee_id(db_session, seed_company.id) == "EMP-1001"

    async def test_increments_from_existing(self, db_session, seed_company):
        await self._add_employee(db_session, seed_company.id, "EMP-1005")
        assert await EmployeeService.generate_employee_id(db_session, seed_company.id) == "EMP-1006"

    async def test_numeric_not_lexicographic(self, db_session, seed_company):
        # A string sort would rank "EMP-999" above "EMP-1000"; the numeric logic must not.
        await self._add_employee(db_session, seed_company.id, "EMP-999")
        await self._add_employee(db_session, seed_company.id, "EMP-1000")
        assert await EmployeeService.generate_employee_id(db_session, seed_company.id) == "EMP-1001"

    async def test_scoped_per_company(self, db_session, seed_company):
        other = uuid.uuid4()
        await self._add_employee(db_session, other, "EMP-2000")
        # Another company's high id must not bump this company's sequence.
        assert await EmployeeService.generate_employee_id(db_session, seed_company.id) == "EMP-1001"


class TestEnterpriseService:
    async def test_get_companies_includes_seed(self, db_session, seed_company):
        companies = await EnterpriseService.get_companies(db_session)
        assert any(c.id == seed_company.id for c in companies)

    async def test_get_jobs_empty(self, db_session, seed_company):
        assert await EnterpriseService.get_jobs(db_session) == []

    async def test_create_job_requirement(self, db_session, seed_company):
        job = await EnterpriseService.create_job_requirement(
            db_session,
            {"title": "SRE", "description": "Keep it up", "status_id": 2, "company_id": seed_company.id},
        )
        assert job.id is not None
        assert job.title == "SRE"

    async def test_get_jobs_returns_created(self, db_session, seed_company):
        db_session.add(JobRequirement(title="X", description="Y", status_id=2, company_id=seed_company.id))
        await db_session.commit()
        jobs = await EnterpriseService.get_jobs(db_session)
        assert len(jobs) == 1

    async def test_publish_job(self, db_session, seed_company):
        job = await EnterpriseService.create_job_requirement(
            db_session, {"title": "Dev", "description": "d", "status_id": 2, "company_id": seed_company.id}
        )
        posting = await EnterpriseService.publish_job(db_session, job.id, "linkedin")
        assert posting.platform == "linkedin"
        assert posting.status == "PUBLISHED"
        assert posting.job_requirement_id == job.id
