import json
from datetime import datetime
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, func, select
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep, PermissionChecker, get_current_user
from app.models.enterprise.x360 import (
    AssignmentStatus,
    CycleStatus,
    X360AssessmentAssignment,
    X360AssessmentCycle,
    X360AssessmentResponse,
    X360AssessmentTemplate,
    X360Question,
    X360TemplateQuestion,
)
from app.models.shared.constants import ModuleScope, PermissionAction
from app.schemas.x360 import (
    X360AIGeneratedQuestion,
    X360AIGenerateRequest,
    X360AssessmentCycleCreate,
    X360AssessmentSubmit,
    X360AssessmentTemplateCreate,
    X360QuestionCreate,
    X360Report,
    X360SummaryStats,
)
from app.schemas.x360 import X360AssessmentAssignment as X360AssessmentAssignmentSchema
from app.schemas.x360 import X360AssessmentCycle as X360AssessmentCycleSchema
from app.schemas.x360 import X360AssessmentTemplate as X360AssessmentTemplateSchema
from app.schemas.x360 import X360Question as X360QuestionSchema
from app.services.x360_service import x360_service

router = APIRouter(prefix="/x360", tags=["Performance 360"])


# Questions
@router.post("/questions", response_model=X360QuestionSchema)
async def create_question(
    request: X360QuestionCreate,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.analytics, PermissionAction.create))
    ],
) -> X360Question:
    company_id = getattr(current_user, "company_id", None)
    if not company_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="User does not belong to a company"
        )

    new_q = X360Question(
        category=request.category, text=request.text, type=request.type, company_id=cast("UUID", company_id)
    )
    db.add(new_q)
    await db.commit()
    await db.refresh(new_q)
    return new_q


@router.get("/questions", response_model=list[X360QuestionSchema])
async def list_questions(
    db: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.analytics, PermissionAction.read))],
) -> list[X360Question]:
    stmt = select(X360Question).where(X360Question.company_id == getattr(current_user, "company_id", None))
    res = await db.execute(stmt)
    return list(res.scalars().all())


@router.post("/questions/ai-generate", response_model=list[X360AIGeneratedQuestion])
async def generate_questions_ai(
    request: X360AIGenerateRequest,
    _current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.analytics, PermissionAction.generate))
    ],
) -> list[X360AIGeneratedQuestion]:
    categories_str = ", ".join(request.categories)
    if request.custom_category:
        categories_str += f", {request.custom_category}"

    prompt = f"""You are an elite Performance Management Consultant.
    Generate {request.count} high-fidelity 360-degree feedback questions
    for the following categories: {categories_str}.
    The target audience is employees in a modern, fast-paced organization.

    Requirements:
    - Questions must be professional, unbiased, and actionable.
    - Mix of RATING (1-5) and TEXT (open-ended).
    - For RATING, no options needed.

    Return ONLY a JSON object with a "questions" key containing a list of question objects:
    {{
      "questions": [
        {{
          "category": "category name",
          "text": "The question text",
          "type": "RATING" or "TEXT"
        }},
        ...
      ]
    }}
    """
    try:
        from app.core.ai import analyze_text_with_llm

        response_str = await analyze_text_with_llm(prompt)

        # Clean markdown
        if "```json" in response_str:
            response_str = response_str.split("```json")[1].split("```")[0].strip()
        elif "```" in response_str:
            response_str = response_str.split("```")[1].split("```")[0].strip()

        data = json.loads(response_str)
        if isinstance(data, dict) and "questions" in data:
            questions_list = data["questions"]
        elif isinstance(data, list):
            questions_list = data
        else:
            questions_list = []

        return [X360AIGeneratedQuestion(**q) for q in questions_list]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI Generation failed: {e!s}") from e


# Templates
@router.post("/templates", response_model=X360AssessmentTemplateSchema)
async def create_template(
    request: X360AssessmentTemplateCreate,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.analytics, PermissionAction.create))
    ],
) -> X360AssessmentTemplate:
    company_id = getattr(current_user, "company_id", None)
    new_tpl = X360AssessmentTemplate(
        name=request.name, description=request.description, company_id=cast("UUID", company_id)
    )
    db.add(new_tpl)
    await db.flush()

    for idx, q_id in enumerate(request.question_ids):
        link = X360TemplateQuestion(template_id=new_tpl.id, question_id=q_id, order=idx)
        db.add(link)

    await db.commit()
    await db.refresh(new_tpl)

    # Reload with questions
    stmt = (
        select(X360AssessmentTemplate)
        .where(X360AssessmentTemplate.id == new_tpl.id)
        .options(selectinload(X360AssessmentTemplate.questions).selectinload(X360TemplateQuestion.question))
    )
    res = await db.execute(stmt)
    return res.scalar_one()


@router.get("/templates", response_model=list[X360AssessmentTemplateSchema])
async def list_templates(
    db: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.analytics, PermissionAction.read))],
) -> list[X360AssessmentTemplate]:
    stmt = (
        select(X360AssessmentTemplate)
        .where(X360AssessmentTemplate.company_id == getattr(current_user, "company_id", None))
        .options(selectinload(X360AssessmentTemplate.questions).selectinload(X360TemplateQuestion.question))
        .order_by(X360AssessmentTemplate.created_at.desc())
    )
    res = await db.execute(stmt)
    return list(res.scalars().all())


@router.put("/templates/{template_id}", response_model=X360AssessmentTemplateSchema)
async def update_template(
    template_id: UUID,
    request: X360AssessmentTemplateCreate,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.analytics, PermissionAction.update))
    ],
) -> X360AssessmentTemplate:
    stmt = select(X360AssessmentTemplate).where(
        X360AssessmentTemplate.id == template_id,
        X360AssessmentTemplate.company_id == getattr(current_user, "company_id", None),
    )
    res = await db.execute(stmt)
    tpl = res.scalar_one_or_none()
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")

    tpl.name = request.name
    tpl.description = request.description

    # Update questions (Simple way: delete and re-add)
    from sqlalchemy import delete

    await db.execute(delete(X360TemplateQuestion).where(X360TemplateQuestion.template_id == template_id))

    for idx, q_id in enumerate(request.question_ids):
        link = X360TemplateQuestion(template_id=template_id, question_id=q_id, order=idx)
        db.add(link)

    await db.commit()
    await db.refresh(tpl)

    # Reload with questions
    stmt_reload = (
        select(X360AssessmentTemplate)
        .where(X360AssessmentTemplate.id == template_id)
        .options(selectinload(X360AssessmentTemplate.questions).selectinload(X360TemplateQuestion.question))
    )
    res_reload = await db.execute(stmt_reload)
    return res_reload.scalar_one()


@router.delete("/templates/{template_id}")
async def delete_template(
    template_id: UUID,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.analytics, PermissionAction.delete))
    ],
) -> dict[str, str]:
    stmt = select(X360AssessmentTemplate).where(
        X360AssessmentTemplate.id == template_id,
        X360AssessmentTemplate.company_id == getattr(current_user, "company_id", None),
    )
    res = await db.execute(stmt)
    tpl = res.scalar_one_or_none()
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")

    await db.delete(tpl)
    await db.commit()
    return {"status": "success"}


# Cycles
@router.post("/cycles", response_model=X360AssessmentCycleSchema)
async def create_and_start_cycle(
    request: X360AssessmentCycleCreate,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.analytics, PermissionAction.create))
    ],
) -> X360AssessmentCycle:
    company_id = getattr(current_user, "company_id", None)
    new_cycle = X360AssessmentCycle(
        name=request.name,
        start_date=request.start_date,
        end_date=request.end_date,
        status=CycleStatus.ACTIVE,
        company_id=cast("UUID", company_id),
    )
    db.add(new_cycle)
    await db.flush()

    # Create assignments for each employee
    from app.models.enterprise.employee import Employee

    # 1. Get all employees in the company
    emp_stmt = select(Employee).where(Employee.company_id == company_id)
    emp_res = await db.execute(emp_stmt)
    employees = emp_res.scalars().all()

    # 2. For each employee, create a self-assessment and manager-assessment (Simplified logic)
    for emp in employees:
        # Self-assessment
        self_assign = X360AssessmentAssignment(
            cycle_id=new_cycle.id,
            ratee_id=emp.id,
            rater_id=emp.id,
            relation="SELF",
            company_id=cast("UUID", company_id),
        )
        db.add(self_assign)

        # Manager assessment (if manager exists)
        if emp.reporting_to_id:
            mgr_assign = X360AssessmentAssignment(
                cycle_id=new_cycle.id,
                ratee_id=emp.id,
                rater_id=emp.reporting_to_id,
                relation="MANAGER",
                company_id=cast("UUID", company_id),
            )
            db.add(mgr_assign)

    await db.commit()
    await db.refresh(new_cycle)
    return new_cycle


@router.get("/cycles/{cycle_id}/progress")
async def get_cycle_progress(
    cycle_id: UUID,
    db: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.analytics, PermissionAction.read))],
) -> list[dict[str, object]]:
    # Verify cycle ownership
    cycle_stmt = select(X360AssessmentCycle).where(
        X360AssessmentCycle.id == cycle_id,
        X360AssessmentCycle.company_id == getattr(current_user, "company_id", None),
    )
    cycle_res = await db.execute(cycle_stmt)
    if not cycle_res.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Cycle not found")

    # Fetch all assignments
    stmt = (
        select(X360AssessmentAssignment)
        .where(X360AssessmentAssignment.cycle_id == cycle_id)
        .options(selectinload(X360AssessmentAssignment.ratee))
    )
    res = await db.execute(stmt)
    assignments = res.scalars().all()

    # Group by ratee
    progress_map: dict[str, dict[str, object]] = {}
    for ass in assignments:
        rid = str(ass.ratee_id)
        if rid not in progress_map:
            progress_map[rid] = {
                "ratee_id": ass.ratee_id,
                "ratee_name": f"{ass.ratee.first_name} {ass.ratee.last_name}" if ass.ratee else "Unknown",
                "completed": 0,
                "total": 0,
                "ai_score": None,
                "breakdown": [],
            }

        progress_map[rid]["total"] = cast("int", progress_map[rid]["total"]) + 1
        if ass.status == AssignmentStatus.COMPLETED:
            progress_map[rid]["completed"] = cast("int", progress_map[rid]["completed"]) + 1

        cast("list[dict[str, object]]", progress_map[rid]["breakdown"]).append(
            {"rater_relation": ass.relation, "status": ass.status}
        )

    # Calculate AI scores for completed ratees
    for _rid, data in progress_map.items():
        if cast("int", data["completed"]) > 0:
            # Fetch report data (which includes AI eval)
            report = await x360_service.get_report(db, cast("UUID", data["ratee_id"]), cycle_id)
            if report and report.get("ai_evaluation"):
                ai_eval = cast("dict[str, object]", report.get("ai_evaluation"))
                data["ai_score"] = ai_eval.get("score")

    return list(progress_map.values())


@router.get("/stats", response_model=X360SummaryStats)
async def get_dashboard_stats(
    db: DBSessionDep, current_user: Annotated[object, Depends(get_current_user)]
) -> dict[str, int]:
    company_id = getattr(current_user, "company_id", None)
    # 1. Active Cycles Count
    cycles_stmt = select(func.count(X360AssessmentCycle.id)).where(
        and_(X360AssessmentCycle.company_id == company_id, X360AssessmentCycle.status == CycleStatus.ACTIVE)
    )
    res_cycles = await db.execute(cycles_stmt)
    active_cycles = res_cycles.scalar() or 0

    # 2. Total Pending My Assessments
    from app.models.enterprise.employee import Employee

    email = getattr(current_user, "email", None)
    emp_stmt = select(Employee.id).where(Employee.email == email)
    emp_res = await db.execute(emp_stmt)
    employee_id = emp_res.scalar_one_or_none()

    pending_my = 0
    completed_my = 0
    if employee_id:
        pending_stmt = select(func.count(X360AssessmentAssignment.id)).where(
            and_(
                X360AssessmentAssignment.rater_id == employee_id,
                X360AssessmentAssignment.status == AssignmentStatus.PENDING,
            )
        )
        res_p = await db.execute(pending_stmt)
        pending_my = res_p.scalar() or 0

        completed_stmt = select(func.count(X360AssessmentAssignment.id)).where(
            and_(
                X360AssessmentAssignment.rater_id == employee_id,
                X360AssessmentAssignment.status == AssignmentStatus.COMPLETED,
            )
        )
        res_c = await db.execute(completed_stmt)
        completed_my = res_c.scalar() or 0

    # 4. Total Participants
    part_stmt = select(func.count(Employee.id)).where(Employee.company_id == company_id)
    res_part = await db.execute(part_stmt)
    total_participants = res_part.scalar() or 0

    return {
        "active_cycles": int(active_cycles),
        "pending_my_assignments": int(pending_my),
        "completed_my_assignments": int(completed_my),
        "total_participants": int(total_participants),
    }


@router.get("/cycles", response_model=list[X360AssessmentCycleSchema])
async def list_cycles(
    db: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.analytics, PermissionAction.read))],
) -> list[X360AssessmentCycle]:
    stmt = select(X360AssessmentCycle).where(
        X360AssessmentCycle.company_id == getattr(current_user, "company_id", None)
    )
    res = await db.execute(stmt)
    return list(res.scalars().all())


# Assignments for current user
@router.get("/my-assessments", response_model=list[X360AssessmentAssignmentSchema])
async def get_my_assessments(
    db: DBSessionDep, current_user: Annotated[object, Depends(get_current_user)]
) -> list[X360AssessmentAssignment]:
    # Need to find the Employee ID associated with current_user.email
    # Assuming logic where EnterpriseUser.email matches Employee.email
    from app.models.enterprise.employee import Employee

    email = getattr(current_user, "email", None)
    if not email:
        return []

    emp_stmt = select(Employee).where(Employee.email == email)
    emp_res = await db.execute(emp_stmt)
    employee = emp_res.scalar_one_or_none()

    if not employee:
        return []

    stmt = (
        select(X360AssessmentAssignment)
        .where(
            X360AssessmentAssignment.rater_id == employee.id,
            X360AssessmentAssignment.status == AssignmentStatus.PENDING,
        )
        .options(
            selectinload(X360AssessmentAssignment.ratee),
            selectinload(X360AssessmentAssignment.rater),
            selectinload(X360AssessmentAssignment.cycle),
        )
    )
    res = await db.execute(stmt)
    return list(res.scalars().all())


@router.get("/assessments/{assignment_id}", response_model=dict[str, object])
async def get_assessment_details(
    assignment_id: UUID,
    db: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.analytics, PermissionAction.read))],
) -> dict[str, object]:
    stmt = (
        select(X360AssessmentAssignment)
        .join(X360AssessmentCycle)
        .where(
            X360AssessmentAssignment.id == assignment_id,
            X360AssessmentCycle.company_id == getattr(current_user, "company_id", None),
        )
        .options(
            selectinload(X360AssessmentAssignment.cycle)
            .selectinload(X360AssessmentCycle.template)
            .selectinload(X360AssessmentTemplate.questions)
            .selectinload(X360TemplateQuestion.question),
            selectinload(X360AssessmentAssignment.ratee),
        )
    )
    res = await db.execute(stmt)
    assignment = res.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    questions = []
    if assignment.cycle and assignment.cycle.template:
        for q in assignment.cycle.template.questions:
            questions.append(
                {
                    "id": q.question.id,
                    "text": q.question.text,
                    "type": q.question.type,
                    "category": q.question.category,
                }
            )

    return {
        "id": assignment.id,
        "ratee_name": f"{assignment.ratee.first_name} {assignment.ratee.last_name}",
        "relation": assignment.relation,
        "questions": questions,
    }


@router.post("/assessments/{assignment_id}/submit")
async def submit_assessment(
    assignment_id: UUID, request: X360AssessmentSubmit, db: DBSessionDep
) -> dict[str, str]:
    assign_stmt = select(X360AssessmentAssignment).where(X360AssessmentAssignment.id == assignment_id)
    res = await db.execute(assign_stmt)
    assignment = res.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    # Save responses
    for resp_data in request.responses:
        resp = X360AssessmentResponse(
            assignment_id=assignment.id,
            question_id=resp_data.question_id,
            answer_value=resp_data.answer_value,
            answer_text=resp_data.answer_text,
        )
        db.add(resp)

    assignment.status = AssignmentStatus.COMPLETED
    assignment.completed_at = cast("Any", datetime.now())
    await db.commit()

    return {"status": "success"}


# Portal (Public Access)
@router.post("/portal/login")
async def portal_login(employee_id: UUID, email: str, db: DBSessionDep) -> dict[str, object]:
    from app.models.enterprise.employee import Employee

    emp_stmt = select(Employee).where(Employee.id == employee_id, Employee.email == email)
    res = await db.execute(emp_stmt)
    emp = res.scalar_one_or_none()

    if not emp:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Generate a simple token (In production, use JWT)
    return {
        "employee_id": str(emp.id),
        "name": f"{emp.first_name} {emp.last_name}",
        "company_id": str(emp.company_id),
    }


@router.get("/portal/assessments/{assignment_id}")
async def get_portal_assessment_details(assignment_id: UUID, db: DBSessionDep) -> dict[str, object]:
    # Public but requires valid assignment ID
    stmt = (
        select(X360AssessmentAssignment)
        .where(X360AssessmentAssignment.id == assignment_id)
        .options(
            selectinload(X360AssessmentAssignment.cycle)
            .selectinload(X360AssessmentCycle.template)
            .selectinload(X360AssessmentTemplate.questions)
            .selectinload(X360TemplateQuestion.question),
            selectinload(X360AssessmentAssignment.ratee),
        )
    )
    res = await db.execute(stmt)
    assignment = res.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    questions = []
    if assignment.cycle and assignment.cycle.template:
        for q in assignment.cycle.template.questions:
            questions.append(
                {
                    "id": q.question.id,
                    "text": q.question.text,
                    "type": q.question.type,
                    "category": q.question.category,
                }
            )

    return {
        "id": assignment.id,
        "ratee_name": f"{assignment.ratee.first_name} {assignment.ratee.last_name}",
        "relation": assignment.relation,
        "questions": questions,
    }


@router.post("/portal/assessments/{assignment_id}/submit")
async def portal_submit_assessment(
    assignment_id: UUID, request: X360AssessmentSubmit, db: DBSessionDep
) -> dict[str, str]:
    """
    Publicly accessible endpoint for portal raters to submit feedback.
    """
    assign_stmt = select(X360AssessmentAssignment).where(X360AssessmentAssignment.id == assignment_id)
    res = await db.execute(assign_stmt)
    assignment = res.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    # Save responses
    for resp_data in request.responses:
        resp = X360AssessmentResponse(
            assignment_id=assignment.id,
            question_id=resp_data.question_id,
            answer_value=resp_data.answer_value,
            answer_text=resp_data.answer_text,
        )
        db.add(resp)

    assignment.status = AssignmentStatus.COMPLETED
    assignment.completed_at = cast("Any", datetime.now())
    await db.commit()

    return {"status": "success"}


@router.get("/report/{employee_id}/{cycle_id}", response_model=X360Report)
async def get_360_report(
    employee_id: UUID,
    cycle_id: UUID,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.analytics, PermissionAction.review))
    ],
) -> X360Report:
    # Verify employee/cycle ownership
    from app.models.enterprise.employee import Employee

    emp_stmt = select(Employee).where(
        Employee.id == employee_id, Employee.company_id == getattr(current_user, "company_id", None)
    )
    res = await db.execute(emp_stmt)
    if not res.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Employee not found")

    report_data = await x360_service.get_report(db, employee_id, cycle_id)
    if not report_data:
        raise HTTPException(status_code=404, detail="Report not generated yet")

    return X360Report(**report_data)


@router.get("/portal/assignments-by-rater/{rater_id}")
async def get_rater_assignments(rater_id: UUID, db: DBSessionDep) -> list[dict[str, object]]:
    stmt = (
        select(X360AssessmentAssignment)
        .where(
            X360AssessmentAssignment.rater_id == rater_id,
            X360AssessmentAssignment.status == AssignmentStatus.PENDING,
        )
        .options(selectinload(X360AssessmentAssignment.ratee), selectinload(X360AssessmentAssignment.cycle))
    )
    res = await db.execute(stmt)
    assignments = res.scalars().all()

    return [
        {
            "assignment_id": a.id,
            "ratee_name": f"{a.ratee.first_name} {a.ratee.last_name}",
            "relation": a.relation,
            "cycle_name": a.cycle.name,
        }
        for a in assignments
    ]
