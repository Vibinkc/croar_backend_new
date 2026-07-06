"""Enterprise-side skill assessments — assign an assessment template to employees
and review their results. The template itself (name/type/topic/questions/duration)
is created and AI-generated via the existing `/assessment-templates` endpoints; this
router only adds the employee assignment + results layer.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.assessment import AssessmentTemplate
from app.models.enterprise.employee import Employee
from app.models.enterprise.skill_assessment import SkillAssessmentAssignment
from app.models.shared.constants import ModuleScope, PermissionAction

router = APIRouter(prefix="/skill-assessments", tags=["Skill Assessments"])


class AssignRequest(BaseModel):
    template_id: UUID
    employee_ids: list[UUID]


def _emp_name(e: Employee | None) -> str:
    if e is None:
        return "Unknown"
    return f"{e.first_name or ''} {e.last_name or ''}".strip() or (e.email or "Unknown")


@router.post("/assign")
async def assign_to_employees(
    payload: AssignRequest,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.assessments, PermissionAction.moderate))
    ],
) -> dict[str, object]:
    """Assign a (question-ready) template to a set of employees. Skips employees who
    already have an assignment for this template so re-assigning is idempotent."""
    company_id = getattr(current_user, "company_id", None)

    template = (
        await db.execute(
            select(AssessmentTemplate).where(
                AssessmentTemplate.id == payload.template_id, AssessmentTemplate.company_id == company_id
            )
        )
    ).scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Assessment template not found.")
    if not template.generated_questions:
        raise HTTPException(
            status_code=400,
            detail="This assessment has no questions yet. Generate questions before assigning it.",
        )

    # Existing assignments for this template → skip (idempotent re-assign).
    existing = {
        row.employee_id
        for row in (
            await db.execute(
                select(SkillAssessmentAssignment.employee_id).where(
                    SkillAssessmentAssignment.template_id == template.id
                )
            )
        ).all()
    }

    assigned = 0
    skipped = 0
    for emp_id in payload.employee_ids:
        # Only assign to employees that belong to this company.
        emp = (
            await db.execute(
                select(Employee.id).where(Employee.id == emp_id, Employee.company_id == company_id)
            )
        ).scalar_one_or_none()
        if emp is None or emp_id in existing:
            skipped += 1
            continue
        db.add(
            SkillAssessmentAssignment(
                template_id=template.id, employee_id=emp_id, company_id=company_id, status="PENDING"
            )
        )
        assigned += 1

    await db.commit()
    return {"assigned": assigned, "skipped": skipped}


@router.get("/assignments")
async def list_assignments(
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.assessments, PermissionAction.read))
    ],
    template_id: UUID | None = None,
) -> list[dict[str, object]]:
    """List employee assignments (optionally for one template) with names, status, score."""
    company_id = getattr(current_user, "company_id", None)
    stmt = (
        select(SkillAssessmentAssignment, Employee, AssessmentTemplate)
        .join(Employee, SkillAssessmentAssignment.employee_id == Employee.id)
        .join(AssessmentTemplate, SkillAssessmentAssignment.template_id == AssessmentTemplate.id)
        .where(SkillAssessmentAssignment.company_id == company_id)
        .order_by(SkillAssessmentAssignment.assigned_at.desc())
    )
    if template_id is not None:
        stmt = stmt.where(SkillAssessmentAssignment.template_id == template_id)

    rows = (await db.execute(stmt)).all()
    return [
        {
            "id": str(a.id),
            "template_id": str(a.template_id),
            "template_name": t.name,
            "topic": t.topic,
            "type": t.type.value if hasattr(t.type, "value") else str(t.type),
            "employee_id": str(a.employee_id),
            "employee_name": _emp_name(e),
            "status": a.status,
            "score": a.score,
            "aptitude_score": a.aptitude_score,
            "coding_score": a.coding_score,
            "assigned_at": a.assigned_at.isoformat() if a.assigned_at else None,
            "completed_at": a.completed_at.isoformat() if a.completed_at else None,
        }
        for a, e, t in rows
    ]


@router.get("/assignments/{assignment_id}/review")
async def review_assignment(
    assignment_id: UUID,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.assessments, PermissionAction.read))
    ],
) -> dict[str, object]:
    """The employee's actual submission — every question with what they answered
    (their code / selected option) next to the correct answer, for HR review."""
    company_id = getattr(current_user, "company_id", None)
    a = (
        await db.execute(
            select(SkillAssessmentAssignment).where(
                SkillAssessmentAssignment.id == assignment_id,
                SkillAssessmentAssignment.company_id == company_id,
            )
        )
    ).scalar_one_or_none()
    if not a:
        raise HTTPException(status_code=404, detail="Assignment not found.")

    template = (
        await db.execute(select(AssessmentTemplate).where(AssessmentTemplate.id == a.template_id))
    ).scalar_one_or_none()
    emp = (await db.execute(select(Employee).where(Employee.id == a.employee_id))).scalar_one_or_none()

    questions = (template.generated_questions if template else []) or []
    answers = a.answers or {}
    review = []
    for q in questions:
        qid = str(q.get("id"))
        qtype = str(q.get("type") or "")
        given = answers.get(qid)
        correct = q.get("correct_answer")
        review.append(
            {
                "id": qid,
                "type": qtype,
                "text": q.get("question") or q.get("problem_statement") or q.get("question_text") or "",
                "options": q.get("options"),
                "correct_answer": correct,
                "answer": given,
                "is_correct": (
                    None
                    if qtype.upper() not in ("APTITUDE", "MCQ")
                    else (given not in (None, "") and str(given) == str(correct))
                ),
            }
        )

    return {
        "id": str(a.id),
        "employee_name": _emp_name(emp),
        "template_name": template.name if template else "",
        "status": a.status,
        "score": a.score,
        "aptitude_score": a.aptitude_score,
        "coding_score": a.coding_score,
        "review": review,
    }


@router.delete("/assignments/{assignment_id}", status_code=204)
async def delete_assignment(
    assignment_id: UUID,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.assessments, PermissionAction.moderate))
    ],
) -> None:
    """Remove an assignment (e.g. assigned by mistake)."""
    company_id = getattr(current_user, "company_id", None)
    a = (
        await db.execute(
            select(SkillAssessmentAssignment).where(
                SkillAssessmentAssignment.id == assignment_id,
                SkillAssessmentAssignment.company_id == company_id,
            )
        )
    ).scalar_one_or_none()
    if not a:
        raise HTTPException(status_code=404, detail="Assignment not found.")
    await db.delete(a)
    await db.commit()
    return
