import json
import uuid
from typing import Annotated, Any, cast

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.ai import analyze_text_with_llm
from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.employee import Employee
from app.models.enterprise.simulation import SimulationAssignment
from app.models.enterprise.survey import SurveyInstance as SurveyInstanceModel
from app.models.enterprise.survey import SurveyInstanceStatus, SurveyInviteStatus
from app.models.enterprise.survey import SurveyInvite as SurveyInviteModel
from app.models.enterprise.survey import SurveyQuestion as SurveyQuestionModel
from app.models.enterprise.survey import SurveyResponse as SurveyResponseModel
from app.models.enterprise.survey import SurveyTemplate as SurveyTemplateModel
from app.models.enterprise.survey import SurveyType as SurveyTypeModel
from app.models.enterprise.x360 import X360AssessmentAssignment
from app.models.shared.constants import ModuleScope, PermissionAction
from app.schemas.survey import (
    QuestionSummary,
    SurveyAIAnalysis,
    SurveyAIGeneratedQuestion,
    SurveyAIGenerateRequest,
    SurveyInstanceCreate,
    SurveyInviteFull,
    SurveyReport,
    SurveySubmission,
    SurveyTemplateCreate,
)
from app.schemas.survey import SurveyInstance as SurveyInstanceSchema
from app.schemas.survey import SurveyTemplate as SurveyTemplateSchema
from app.schemas.survey import SurveyType as SurveyTypeSchema
from app.services.enterprise.survey_service import survey_service

router = APIRouter(prefix="/surveys", tags=["Surveys"])


@router.get("/types", response_model=list[SurveyTypeSchema])
async def list_survey_types(
    db: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.surveys, PermissionAction.read))],
) -> list[SurveyTypeModel]:
    stmt = (
        select(SurveyTypeModel)
        .where(
            (SurveyTypeModel.company_id == getattr(current_user, "company_id", None))
            | (SurveyTypeModel.company_id.is_(None))
        )
        .order_by(SurveyTypeModel.name)
    )
    res = await db.execute(stmt)
    return list(res.scalars().all())


@router.post("/ai-generate-questions", response_model=list[SurveyAIGeneratedQuestion])
async def ai_generate_survey_questions(
    request: SurveyAIGenerateRequest,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.surveys, PermissionAction.generate))
    ],
) -> list[SurveyAIGeneratedQuestion]:
    """
    Generates industry-specific survey questions using AI.
    """
    # 1. Get the survey type name
    stmt = select(SurveyTypeModel).where(
        SurveyTypeModel.id == request.survey_type_id,
        (SurveyTypeModel.company_id == getattr(current_user, "company_id", None))
        | (SurveyTypeModel.company_id.is_(None)),
    )
    res = await db.execute(stmt)
    st = res.scalar_one_or_none()
    if not st:
        raise HTTPException(status_code=404, detail="Survey Type not found")

    prompt = f"""You are an elite HR Strategy Consultant. Generate {request.count}
high-fidelity survey questions specifically for the {request.industry_nature} industry.
The survey type is: {st.name}.

Requirements:
- Mix of RATING (Scale 1-5), TEXT (Open ended), and MCQ (Multiple Choice).
- Questions must be professional, culturally sensitive, and relevant to {request.industry_nature}.
- For MCQ, provide exactly 4 distinct options.

Return ONLY a JSON object:
{{
  "questions": [
    {{
      "text": "The question text",
      "type": "RATING" | "TEXT" | "MCQ",
      "options": ["Option 1", "Option 2", "Option 3", "Option 4"] // ONLY if type is MCQ, else null
    }},
    ...
  ]
}}
"""
    try:
        response_str = await analyze_text_with_llm(prompt)
        # Handle cases where response might be wrapped in markdown
        if "```json" in response_str:
            response_str = response_str.split("```json")[1].split("```")[0].strip()
        elif "```" in response_str:
            response_str = response_str.split("```")[1].split("```")[0].strip()

        data = json.loads(response_str)
        # Extract questions from the nested field if present, otherwise assume data is the list
        questions_list = cast(
            "list[dict[str, object]]", data.get("questions", data) if isinstance(data, dict) else data
        )
        return [SurveyAIGeneratedQuestion(**q) for q in questions_list]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI Generation failed: {e!s}") from e


@router.post("/templates", response_model=SurveyTemplateSchema)
async def create_template(
    request: SurveyTemplateCreate,
    db: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.surveys, PermissionAction.create))],
) -> SurveyTemplateModel:
    new_tpl = SurveyTemplateModel(
        survey_type_id=request.survey_type_id,
        title=request.title,
        description=request.description,
        is_active=request.is_active,
        company_id=cast("uuid.UUID", getattr(current_user, "company_id", None)),
    )
    db.add(new_tpl)
    await db.flush()

    for idx, q_data in enumerate(request.questions):
        new_q = SurveyQuestionModel(
            template_id=new_tpl.id,
            text=q_data.text,
            type=q_data.type,
            order=idx,
            scale_min=q_data.scale_min,
            scale_max=q_data.scale_max,
            options=q_data.options,
            company_id=cast("uuid.UUID", getattr(current_user, "company_id", None)),
        )
        db.add(new_q)

    await db.commit()

    # Return with relationships
    stmt = (
        select(SurveyTemplateModel)
        .where(SurveyTemplateModel.id == new_tpl.id)
        .options(selectinload(SurveyTemplateModel.questions), selectinload(SurveyTemplateModel.survey_type))
    )
    res = await db.execute(stmt)
    return res.scalar_one()


@router.get("/templates", response_model=list[SurveyTemplateSchema])
async def list_templates(
    db: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.surveys, PermissionAction.read))],
) -> list[SurveyTemplateModel]:
    stmt = (
        select(SurveyTemplateModel)
        .where(SurveyTemplateModel.company_id == getattr(current_user, "company_id", None))
        .options(selectinload(SurveyTemplateModel.questions), selectinload(SurveyTemplateModel.survey_type))
        .order_by(SurveyTemplateModel.created_at.desc())
    )
    res = await db.execute(stmt)
    return list(res.scalars().all())


@router.post("/launch", response_model=SurveyInstanceSchema)
async def launch_survey(
    request: SurveyInstanceCreate,
    db: DBSessionDep,
    background_tasks: BackgroundTasks,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.surveys, PermissionAction.create))],
) -> SurveyInstanceModel:
    new_instance = SurveyInstanceModel(
        template_id=request.template_id,
        name=request.name,
        start_date=request.start_date,
        end_date=request.end_date,
        status=SurveyInstanceStatus.ACTIVE,
        target_group=request.target_group,
        company_id=cast("uuid.UUID", getattr(current_user, "company_id", None)),
    )
    db.add(new_instance)
    await db.flush()

    # Determine target employees
    target_ids: list[uuid.UUID] = []
    if request.target_group == "ALL":
        ids_stmt = select(Employee.id).where(Employee.company_id == getattr(current_user, "company_id", None))
        res_ids = await db.execute(ids_stmt)
        target_ids = list(res_ids.scalars().all())
    elif request.target_group == "CUSTOM" and request.employee_ids:
        target_ids = request.employee_ids

    # Create Invites
    for emp_id in target_ids:
        new_invite = SurveyInviteModel(
            instance_id=new_instance.id,
            employee_id=emp_id,
            status=SurveyInviteStatus.PENDING,
            company_id=cast("uuid.UUID", getattr(current_user, "company_id", None)),
        )
        db.add(new_invite)

    await db.commit()

    # Notify in background
    background_tasks.add_task(survey_service.notify_participants, db, new_instance.id)

    # Reload with relationships
    stmt_reload = (
        select(SurveyInstanceModel)
        .where(SurveyInstanceModel.id == new_instance.id)
        .options(
            selectinload(SurveyInstanceModel.template).selectinload(SurveyTemplateModel.survey_type),
            selectinload(SurveyInstanceModel.template).selectinload(SurveyTemplateModel.questions),
        )
    )
    res_reload = await db.execute(stmt_reload)
    return res_reload.scalar_one()


@router.get("/invites", response_model=list[SurveyInviteFull])
async def list_my_invites(
    db: DBSessionDep,
    employee_id: uuid.UUID = Query(...),  # In production, this would be from employee auth
) -> list[SurveyInviteModel]:
    stmt = (
        select(SurveyInviteModel)
        .where(
            SurveyInviteModel.employee_id == employee_id,
            SurveyInviteModel.status == SurveyInviteStatus.PENDING,
        )
        .options(
            selectinload(SurveyInviteModel.instance)
            .selectinload(SurveyInstanceModel.template)
            .selectinload(SurveyTemplateModel.questions)
        )
    )
    res = await db.execute(stmt)
    return list(res.scalars().all())


@router.get("/invite/{token}", response_model=SurveyInviteFull)
async def get_invite_by_token(token: str, db: DBSessionDep) -> SurveyInviteModel:
    stmt = (
        select(SurveyInviteModel)
        .where(SurveyInviteModel.token == token)
        .options(
            selectinload(SurveyInviteModel.instance)
            .selectinload(SurveyInstanceModel.template)
            .selectinload(SurveyTemplateModel.questions),
            selectinload(SurveyInviteModel.instance)
            .selectinload(SurveyInstanceModel.template)
            .selectinload(SurveyTemplateModel.survey_type),
            selectinload(SurveyInviteModel.employee),
        )
    )
    res = await db.execute(stmt)
    invite = res.scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")
    return invite


@router.post("/submit/{token}")
async def submit_survey(token: str, submission: SurveySubmission, db: DBSessionDep) -> dict[str, str]:
    stmt = select(SurveyInviteModel).where(SurveyInviteModel.token == token)
    res = await db.execute(stmt)
    invite = res.scalar_one_or_none()

    if not invite or invite.status == SurveyInviteStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Invalid or already completed invite")

    for resp in submission.responses:
        db.add(
            SurveyResponseModel(
                invite_id=invite.id,
                question_id=resp.question_id,
                answer_value=resp.answer_value,
                answer_text=resp.answer_text,
                company_id=invite.company_id,
            )
        )

    invite.status = SurveyInviteStatus.COMPLETED
    invite.completed_at = cast("Any", func.now())
    await db.commit()
    return {"message": "Survey submitted successfully"}


@router.get("/instances", response_model=list[SurveyInstanceSchema])
async def list_instances(
    db: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.surveys, PermissionAction.read))],
) -> list[SurveyInstanceModel]:
    stmt = (
        select(SurveyInstanceModel)
        .where(SurveyInstanceModel.company_id == getattr(current_user, "company_id", None))
        .options(
            selectinload(SurveyInstanceModel.template).selectinload(SurveyTemplateModel.survey_type),
            selectinload(SurveyInstanceModel.template).selectinload(SurveyTemplateModel.questions),
        )
        .order_by(SurveyInstanceModel.created_at.desc())
    )
    res = await db.execute(stmt)
    return list(res.scalars().all())


@router.get("/report/{instance_id}", response_model=SurveyReport)
async def get_survey_report(
    instance_id: uuid.UUID,
    db: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.surveys, PermissionAction.review))],
) -> SurveyReport:
    # Check instance
    stmt = (
        select(SurveyInstanceModel)
        .where(
            SurveyInstanceModel.id == instance_id,
            SurveyInstanceModel.company_id == getattr(current_user, "company_id", None),
        )
        .options(selectinload(SurveyInstanceModel.template).selectinload(SurveyTemplateModel.questions))
    )
    res = await db.execute(stmt)
    instance = res.scalar_one_or_none()
    if not instance:
        raise HTTPException(status_code=404, detail="Survey instance not found")

    # Invite stats
    stmt_stats = select(
        func.count(SurveyInviteModel.id),
        func.count(SurveyInviteModel.id).filter(SurveyInviteModel.status == SurveyInviteStatus.COMPLETED),
    ).where(SurveyInviteModel.instance_id == instance_id)
    res_stats = await db.execute(stmt_stats)
    total, completed = cast("tuple[int, int]", res_stats.one())

    # Question breakdown
    summaries = []
    for q in instance.template.questions:
        # Fetch all responses for this question in this instance
        stmt_resp = (
            select(SurveyResponseModel)
            .join(SurveyInviteModel)
            .where(SurveyInviteModel.instance_id == instance_id, SurveyResponseModel.question_id == q.id)
        )
        res_resp = await db.execute(stmt_resp)
        responses = cast("list[SurveyResponseModel]", list(res_resp.scalars().all()))

        summary = QuestionSummary(
            question_id=q.id, question_text=q.text, question_type=q.type, response_count=len(responses)
        )

        if q.type == "RATING":
            vals = [r.answer_value for r in responses if r.answer_value is not None]
            if vals:
                summary.average_score = sum(vals) / len(vals)
                dist: dict[str, object] = {}
                for v in vals:
                    current_count = cast("int", dist.get(str(v), 0))
                    dist[str(v)] = current_count + 1
                summary.distribution = dist
        elif q.type == "TEXT":
            summary.text_responses = [r.answer_text for r in responses if r.answer_text]

        summaries.append(summary)

    return SurveyReport(
        instance_id=instance.id,
        instance_name=instance.name,
        total_invites=total,
        completed_invites=completed,
        questions=summaries,
    )


@router.post("/report/{instance_id}/ai-analysis", response_model=SurveyAIAnalysis)
async def generate_survey_report_ai(
    instance_id: uuid.UUID,
    db: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.surveys, PermissionAction.review))],
) -> SurveyAIAnalysis:
    """
    Generates strategic AI insights based on aggregated survey data.
    """
    # 1. Generate the standard report data first
    report = await get_survey_report(instance_id, db, current_user)

    # 2. Build a condensed context for the LLM
    context_parts = [f"Survey Report: {report.instance_name}"]
    context_parts.append(f"Total Audience: {report.total_invites}, Responses: {report.completed_invites}")

    for q in report.questions:
        q_ctx = f"Q: {q.question_text}"
        if q.average_score:
            q_ctx += f" | Avg Score: {q.average_score}/5"
        if q.text_responses:
            # Take top 3 text responses to save tokens
            q_ctx += f" | Comments: {'; '.join(q.text_responses[:3])}"
        if q.distribution:
            q_ctx += f" | Distribution: {q.distribution}"
        context_parts.append(q_ctx)

    context_str = "\n".join(context_parts)

    prompt = f"""You are an expert Organizational Psychologist and Management Consultant.
Analyze the following aggregated employee survey data and provide a high-fidelity strategic evaluation.

Data:
{context_str}

Return ONLY a JSON object:
{{
  "summary": "A professional executive summary of the organizational health (2-3 sentences).",
  "performance_score": number_between_0_and_100,
  "strengths": ["Strength 1", "Strength 2", "Strength 3"],
  "weaknesses": ["Area for Improvement 1", "Area for Improvement 2"],
  "recommendations": ["Strategic Recommendation 1", "Strategic Recommendation 2"]
}}
"""

    try:
        response_str = await analyze_text_with_llm(prompt)
        # Handle cases where response might be wrapped in markdown
        if "```json" in response_str:
            response_str = response_str.split("```json")[1].split("```")[0].strip()
        elif "```" in response_str:
            response_str = response_str.split("```")[1].split("```")[0].strip()

        data = json.loads(response_str)
        return SurveyAIAnalysis(**data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI Analysis failed: {e!s}") from e


@router.post("/instances/{instance_id}/notify")
async def notify_instance_participants(
    instance_id: uuid.UUID,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.surveys, PermissionAction.moderate))
    ],
) -> dict[str, str]:
    # Verify instance ownership
    stmt = select(SurveyInstanceModel).where(
        SurveyInstanceModel.id == instance_id,
        SurveyInstanceModel.company_id == getattr(current_user, "company_id", None),
    )
    res = await db.execute(stmt)
    if not res.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Survey instance not found")

    count = await survey_service.notify_participants(db, instance_id, only_pending=True)
    return {"message": f"Successfully notified {count} pending participants"}


@router.post("/invites/{invite_id}/resend")
async def resend_survey_invite(
    invite_id: uuid.UUID,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.surveys, PermissionAction.moderate))
    ],
) -> dict[str, str]:
    stmt = (
        select(SurveyInviteModel)
        .join(SurveyInstanceModel)
        .where(
            SurveyInviteModel.id == invite_id,
            SurveyInstanceModel.company_id == getattr(current_user, "company_id", None),
        )
        .options(selectinload(SurveyInviteModel.employee), selectinload(SurveyInviteModel.instance))
    )
    res = await db.execute(stmt)
    invite = res.scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")

    success = await survey_service.send_invite_email(invite)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to send email")

    return {"message": "Invite resent successfully"}


@router.post("/portal/login")
async def unified_portal_login(employee_id: uuid.UUID, email: str, db: DBSessionDep) -> dict[str, object]:
    """
    Unified entry point for employees to see both 360 and Survey tasks.
    """
    # 1. Verify Employee
    stmt = select(Employee).where(Employee.id == employee_id, Employee.email == email)
    emp_res = await db.execute(stmt)
    emp = emp_res.scalar_one_or_none()

    if not emp:
        raise HTTPException(status_code=401, detail="Invalid Employee ID or Email")

    # 2. Fetch 360 Assignments
    # IMPORTANT: Accessing X360AssessmentAssignment.status which is an Enum
    x360_stmt = (
        select(X360AssessmentAssignment)
        .where(X360AssessmentAssignment.rater_id == emp.id, X360AssessmentAssignment.status == "PENDING")
        .options(selectinload(X360AssessmentAssignment.ratee), selectinload(X360AssessmentAssignment.cycle))
    )
    x360_res = await db.execute(x360_stmt)
    x360_tasks = x360_res.scalars().all()

    # 3. Fetch Survey Invites
    survey_stmt = (
        select(SurveyInviteModel)
        .where(
            SurveyInviteModel.employee_id == emp.id, SurveyInviteModel.status == SurveyInviteStatus.PENDING
        )
        .options(selectinload(SurveyInviteModel.instance).selectinload(SurveyInstanceModel.template))
    )
    survey_res = await db.execute(survey_stmt)
    survey_tasks = survey_res.scalars().all()

    # 4. Fetch Simulation Assignments
    sim_stmt = (
        select(SimulationAssignment)
        .where(SimulationAssignment.employee_id == emp.id, SimulationAssignment.status != "COMPLETED")
        .options(selectinload(SimulationAssignment.scenario))
    )
    sim_res = await db.execute(sim_stmt)
    sim_tasks = sim_res.scalars().all()

    return {
        "employee": {
            "id": str(emp.id),
            "first_name": emp.first_name,
            "last_name": emp.last_name,
            "email": emp.email,
        },
        "x360_assignments": [
            {
                "id": str(t.id),
                "relation": t.relation,
                "ratee_name": f"{t.ratee.first_name} {t.ratee.last_name}",
                "cycle_name": t.cycle.name,
            }
            for t in x360_tasks
        ],
        "survey_invites": [
            {
                "id": str(t.id),
                "token": t.token,
                "instance_name": t.instance.name,
                "template_title": t.instance.template.title if t.instance.template else "Standard Framework",
            }
            for t in survey_tasks
        ],
        "simulation_assignments": [
            {
                "id": str(t.id),
                "scenario_id": str(t.scenario_id),
                "title": t.scenario.title,
                "description": t.scenario.description,
                "category": t.scenario.category,
                "character": f"{t.scenario.character_name} ({t.scenario.character_role})",
            }
            for t in sim_tasks
        ],
    }
