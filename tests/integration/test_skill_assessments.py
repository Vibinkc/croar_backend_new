"""Integration tests for employee Skill Assessments.

Enterprise side  (/api/v1/enterprise/skill-assessments): assign a template to
employees, list results, review a submission, delete an assignment.
Employee side    (/api/v1/me/skill-assessments): list assigned, fetch questions
(correct answers stripped), submit + auto-grade, single-submission guard.
"""

import uuid

from app.models.shared.constants import ModuleScope, PermissionAction

ENT = "/api/v1/enterprise/skill-assessments"
ME = "/api/v1/me/skill-assessments"

READ = [(ModuleScope.assessments, PermissionAction.read)]
MODERATE = [(ModuleScope.assessments, PermissionAction.moderate)]

# One aptitude question with a known correct answer -> deterministic grading (no AI).
QUESTIONS = [
    {"id": "q1", "type": "APTITUDE", "question": "2 + 2?", "options": ["3", "4", "5"], "correct_answer": "4"},
    {
        "id": "q2",
        "type": "APTITUDE",
        "question": "Capital of France?",
        "options": ["Paris", "Rome"],
        "correct_answer": "Paris",
    },
]

TESTER_EMAIL = "tester@example.com"  # matches conftest.make_auth_user's email


async def _seed_template(db, company_id, questions=QUESTIONS):
    from app.models.enterprise.assessment import AssessmentTemplate, AssessmentType

    t = AssessmentTemplate(
        name="Frontend Skills",
        type=AssessmentType.APTITUDE,
        topic="React",
        company_id=company_id,
        generated_questions=questions,
        question_count=len(questions or []),
        test_duration=15,
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


async def _seed_employee(db, company_id, email=TESTER_EMAIL):
    from app.models.enterprise.employee import Employee

    e = Employee(
        employee_id=f"EMP-{uuid.uuid4().hex[:6]}",
        first_name="Test",
        last_name="User",
        email=email,
        company_id=company_id,
    )
    db.add(e)
    await db.commit()
    await db.refresh(e)
    return e


async def _seed_assignment(db, template_id, employee_id, company_id, status="PENDING", answers=None):
    from app.models.enterprise.skill_assessment import SkillAssessmentAssignment

    a = SkillAssessmentAssignment(
        template_id=template_id,
        employee_id=employee_id,
        company_id=company_id,
        status=status,
        answers=answers,
    )
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return a


# ── Enterprise: assign ──────────────────────────────────────────────────────
class TestAssign:
    async def test_unauthenticated_401(self, client):
        r = await client.post(f"{ENT}/assign", json={"template_id": str(uuid.uuid4()), "employee_ids": []})
        assert r.status_code == 401

    async def test_wrong_permission_403(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=READ))
        r = await client.post(f"{ENT}/assign", json={"template_id": str(uuid.uuid4()), "employee_ids": []})
        assert r.status_code == 403

    async def test_template_not_found_404(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=MODERATE))
        r = await client.post(f"{ENT}/assign", json={"template_id": str(uuid.uuid4()), "employee_ids": []})
        assert r.status_code == 404

    async def test_template_without_questions_400(self, client, db_session, seed_company, as_user, auth_user):
        tpl = await _seed_template(db_session, seed_company.id, questions=None)
        emp = await _seed_employee(db_session, seed_company.id)
        as_user(auth_user(seed_company.id, perms=MODERATE))
        r = await client.post(
            f"{ENT}/assign", json={"template_id": str(tpl.id), "employee_ids": [str(emp.id)]}
        )
        assert r.status_code == 400

    async def test_happy_path_and_idempotent(self, client, db_session, seed_company, as_user, auth_user):
        tpl = await _seed_template(db_session, seed_company.id)
        emp = await _seed_employee(db_session, seed_company.id)
        as_user(auth_user(seed_company.id, perms=MODERATE))
        r = await client.post(
            f"{ENT}/assign", json={"template_id": str(tpl.id), "employee_ids": [str(emp.id)]}
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"assigned": 1, "skipped": 0}
        # Re-assigning the same employee is skipped (idempotent).
        r2 = await client.post(
            f"{ENT}/assign", json={"template_id": str(tpl.id), "employee_ids": [str(emp.id)]}
        )
        assert r2.json() == {"assigned": 0, "skipped": 1}


# ── Enterprise: list + review ───────────────────────────────────────────────
class TestListAndReview:
    async def test_list_returns_assignments(self, client, db_session, seed_company, as_user, auth_user):
        tpl = await _seed_template(db_session, seed_company.id)
        emp = await _seed_employee(db_session, seed_company.id)
        await _seed_assignment(db_session, tpl.id, emp.id, seed_company.id)
        as_user(auth_user(seed_company.id, perms=READ))
        r = await client.get(f"{ENT}/assignments?template_id={tpl.id}")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["employee_name"] == "Test User"
        assert body[0]["status"] == "PENDING"

    async def test_review_shows_answers_and_correctness(
        self, client, db_session, seed_company, as_user, auth_user
    ):
        tpl = await _seed_template(db_session, seed_company.id)
        emp = await _seed_employee(db_session, seed_company.id)
        # Answered q1 correctly, q2 wrong.
        a = await _seed_assignment(
            db_session, tpl.id, emp.id, seed_company.id, status="COMPLETED", answers={"q1": "4", "q2": "Rome"}
        )
        as_user(auth_user(seed_company.id, perms=READ))
        r = await client.get(f"{ENT}/assignments/{a.id}/review")
        assert r.status_code == 200, r.text
        review = {item["id"]: item for item in r.json()["review"]}
        assert review["q1"]["answer"] == "4" and review["q1"]["is_correct"] is True
        assert review["q2"]["answer"] == "Rome" and review["q2"]["is_correct"] is False
        assert review["q1"]["correct_answer"] == "4"

    async def test_review_not_found_404(self, client, seed_company, as_user, auth_user):
        as_user(auth_user(seed_company.id, perms=READ))
        assert (await client.get(f"{ENT}/assignments/{uuid.uuid4()}/review")).status_code == 404

    async def test_delete_assignment_204(self, client, db_session, seed_company, as_user, auth_user):
        tpl = await _seed_template(db_session, seed_company.id)
        emp = await _seed_employee(db_session, seed_company.id)
        a = await _seed_assignment(db_session, tpl.id, emp.id, seed_company.id)
        as_user(auth_user(seed_company.id, perms=MODERATE))
        assert (await client.delete(f"{ENT}/assignments/{a.id}")).status_code == 204


# ── Employee self-service (/me) ─────────────────────────────────────────────
class TestEmployeeFlow:
    async def test_list_and_detail_strips_correct_answer(
        self, client, db_session, seed_company, as_user, auth_user
    ):
        tpl = await _seed_template(db_session, seed_company.id)
        emp = await _seed_employee(db_session, seed_company.id)  # email == tester@example.com
        a = await _seed_assignment(db_session, tpl.id, emp.id, seed_company.id)
        as_user(auth_user(seed_company.id))  # /me needs auth only, no RBAC perm

        listed = await client.get(ME)
        assert listed.status_code == 200, listed.text
        assert listed.json()[0]["question_count"] == 2

        detail = await client.get(f"{ME}/{a.id}")
        assert detail.status_code == 200
        q = detail.json()["questions"][0]
        assert "correct_answer" not in q  # never leak the answer key to the taker
        assert q["options"] == ["3", "4", "5"]

    async def test_submit_grades_and_blocks_resubmit(
        self, client, db_session, seed_company, as_user, auth_user
    ):
        tpl = await _seed_template(db_session, seed_company.id)
        emp = await _seed_employee(db_session, seed_company.id)
        a = await _seed_assignment(db_session, tpl.id, emp.id, seed_company.id)
        as_user(auth_user(seed_company.id))

        # Both correct -> 100.
        r = await client.post(f"{ME}/{a.id}/submit", json={"q1": "4", "q2": "Paris"})
        assert r.status_code == 200, r.text
        assert r.json()["score"] == 100
        # Single submission only.
        r2 = await client.post(f"{ME}/{a.id}/submit", json={"q1": "4", "q2": "Paris"})
        assert r2.status_code == 409

    async def test_blank_submission_scores_zero(self, client, db_session, seed_company, as_user, auth_user):
        tpl = await _seed_template(db_session, seed_company.id)
        emp = await _seed_employee(db_session, seed_company.id)
        a = await _seed_assignment(db_session, tpl.id, emp.id, seed_company.id)
        as_user(auth_user(seed_company.id))
        r = await client.post(f"{ME}/{a.id}/submit", json={})
        assert r.status_code == 200, r.text
        assert r.json()["score"] == 0

    async def test_cannot_open_other_employees_assignment_404(
        self, client, db_session, seed_company, as_user, auth_user
    ):
        tpl = await _seed_template(db_session, seed_company.id)
        await _seed_employee(db_session, seed_company.id)  # the caller (tester@example.com)
        other = await _seed_employee(db_session, seed_company.id, email="someone.else@example.com")
        a = await _seed_assignment(db_session, tpl.id, other.id, seed_company.id)
        as_user(auth_user(seed_company.id))
        assert (await client.get(f"{ME}/{a.id}")).status_code == 404
