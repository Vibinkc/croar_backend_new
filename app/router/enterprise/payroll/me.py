"""Employee self-service endpoints (/api/v1/me).

Scoped to the signed-in user's own linked Employee record (see
`get_current_employee_id`). These are deliberately separate from the company-wide
`/enterprise/*` routes: an EMPLOYEE-role user holds only `self:read` and can
reach nothing here that isn't their own.

Read: own timesheets, own released payslips, own leave balances/history.
Write: file (and cancel) one's own leave request — the employee_id is taken from
the link, never the payload, so a user can only ever act on themselves.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.enterprise.assessment import AssessmentTemplate
from app.models.enterprise.employee import Employee
from app.models.enterprise.skill_assessment import SkillAssessmentAssignment
from app.models.enterprise.survey import SurveyInstance as SurveyInstanceModel
from app.models.enterprise.survey import SurveyInvite as SurveyInviteModel
from app.models.enterprise.survey import SurveyInviteStatus
from app.models.enterprise.survey import SurveyResponse as SurveyResponseModel
from app.models.enterprise.survey import SurveyTemplate as SurveyTemplateModel
from app.models.enterprise.user_role import EnterpriseUser as User
from app.models.enterprise.x360 import (
    AssignmentStatus,
    X360AssessmentAssignment,
    X360AssessmentCycle,
    X360AssessmentResponse,
    X360AssessmentTemplate,
    X360TemplateQuestion,
)
from app.models.payroll import PayrollCycle, Payslip
from app.models.payroll.leave import LeaveType
from app.payroll.constants import DEFAULT_FINANCIAL_YEAR, PayrollCycleStatus, Permission
from app.payroll.deps import DBSessionDep, get_current_company_id, get_current_employee_id, require_permission
from app.schemas.enterprise.payroll.leave import (
    LeaveBalanceOut,
    LeaveDecisionIn,
    LeaveRequestIn,
    LeaveRequestOut,
    LeaveTypeOut,
    MyLeaveRequestIn,
)
from app.schemas.enterprise.payroll.payroll import MyPayslipOut
from app.schemas.enterprise.payroll.timesheets import (
    TimesheetBulkEntryUpdate,
    TimesheetDetailOut,
    TimesheetOut,
    TimesheetSummaryOut,
)
from app.schemas.survey import SurveySubmission
from app.schemas.x360 import X360AssessmentSubmit
from app.services.enterprise.assessment_grading import grade_assessment
from app.services.payroll import leave_service, timesheet_service

router = APIRouter(prefix="/api/v1/me", tags=["self-service"])


def _employee_label(emp: Employee | None) -> tuple[str | None, str | None]:
    if emp is None:
        return None, None
    name = f"{emp.first_name} {emp.last_name}".strip() or emp.email
    return name, emp.employee_id


# ---------------------------------------------------------------------------
# Timesheets
# ---------------------------------------------------------------------------
@router.get("/timesheets", response_model=list[TimesheetSummaryOut])
async def my_timesheets(
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    employee_id: uuid.UUID = Depends(get_current_employee_id),
) -> list[TimesheetSummaryOut]:
    """List the signed-in employee's own timesheets (all cycles)."""
    rows = await timesheet_service.list_for_employee(db, company_id, employee_id)
    emp = (await db.execute(select(Employee).where(Employee.id == employee_id))).scalar_one_or_none()
    name, code = _employee_label(emp)
    return [
        TimesheetSummaryOut(
            **TimesheetOut.model_validate(ts).model_dump(), employee_name=name, employee_code=code
        )
        for ts in rows
    ]


async def _timesheet_detail_out(db: DBSessionDep, ts) -> TimesheetDetailOut:
    """Build the detail response (employee + actor display names) for a timesheet."""
    emp = (await db.execute(select(Employee).where(Employee.id == ts.employee_id))).scalar_one_or_none()
    name, code = _employee_label(emp)
    actor_ids = [i for i in (ts.submitted_by_id, ts.approved_by_id) if i is not None]
    users = (
        {
            u.id: (u.full_name or u.email)
            for u in (await db.execute(select(User).where(User.id.in_(actor_ids)))).scalars().all()
        }
        if actor_ids
        else {}
    )
    out = TimesheetDetailOut.model_validate(ts, from_attributes=True)
    return out.model_copy(
        update={
            "employee_name": name,
            "employee_code": code,
            "submitted_by_name": users.get(ts.submitted_by_id),
            "approved_by_name": users.get(ts.approved_by_id),
        }
    )


@router.get("/timesheets/{timesheet_id}", response_model=TimesheetDetailOut)
async def my_timesheet_detail(
    timesheet_id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    employee_id: uuid.UUID = Depends(get_current_employee_id),
) -> TimesheetDetailOut:
    """View one of the signed-in employee's own timesheets (404 if not theirs)."""
    ts = await timesheet_service.get_owned_detail(db, timesheet_id, company_id, employee_id)
    return await _timesheet_detail_out(db, ts)


@router.put("/timesheets/{timesheet_id}/mark", response_model=TimesheetDetailOut)
async def mark_my_attendance(
    timesheet_id: uuid.UUID,
    payload: TimesheetBulkEntryUpdate,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    employee_id: uuid.UUID = Depends(get_current_employee_id),
) -> TimesheetDetailOut:
    """Self-mark attendance on one's own draft timesheet (Present/WFH + hours).
    Guarded server-side: own sheet only, editable status, no future dates, no
    self-LOP, and leave days are protected. HR still submits/approves."""
    ts = await timesheet_service.self_mark_entries(
        db,
        timesheet_id,
        company_id,
        employee_id,
        payload.entries,
        today=datetime.now(UTC).replace(tzinfo=None).date(),
    )
    return await _timesheet_detail_out(db, ts)


# ---------------------------------------------------------------------------
# Payslips — only RELEASED (cycle PAID) payslips are visible, mirroring the
# enterprise PAID gate on get_payslip.
# ---------------------------------------------------------------------------
def _my_payslip_out(payslip: Payslip, cycle: PayrollCycle) -> MyPayslipOut:
    return MyPayslipOut.model_validate(payslip, from_attributes=True).model_copy(
        update={
            "cycle_name": cycle.name,
            "period_start": cycle.period_start,
            "period_end": cycle.period_end,
            "pay_date": cycle.pay_date,
        }
    )


@router.get("/payslips", response_model=list[MyPayslipOut])
async def my_payslips(
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    employee_id: uuid.UUID = Depends(get_current_employee_id),
) -> list[MyPayslipOut]:
    """The signed-in employee's released payslips (cycle PAID), newest first."""
    rows = (
        await db.execute(
            select(Payslip, PayrollCycle)
            .join(PayrollCycle, Payslip.cycle_id == PayrollCycle.id)
            .where(
                Payslip.company_id == company_id,
                Payslip.employee_id == employee_id,
                PayrollCycle.status == PayrollCycleStatus.PAID.value,
                PayrollCycle.deleted_at.is_(None),
            )
            .order_by(PayrollCycle.period_start.desc())
        )
    ).all()
    return [_my_payslip_out(ps, cyc) for ps, cyc in rows]


@router.get("/payslips/{payslip_id}", response_model=MyPayslipOut)
async def my_payslip_detail(
    payslip_id: uuid.UUID,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    employee_id: uuid.UUID = Depends(get_current_employee_id),
) -> MyPayslipOut:
    """View one of the employee's own released payslips (404 if not theirs/unpaid)."""
    row = (
        await db.execute(
            select(Payslip, PayrollCycle)
            .join(PayrollCycle, Payslip.cycle_id == PayrollCycle.id)
            .where(
                Payslip.id == payslip_id,
                Payslip.company_id == company_id,
                Payslip.employee_id == employee_id,
                PayrollCycle.status == PayrollCycleStatus.PAID.value,
                PayrollCycle.deleted_at.is_(None),
            )
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payslip not found.")
    return _my_payslip_out(row[0], row[1])


# ---------------------------------------------------------------------------
# Leave — own balances, history, and self-service apply/cancel
# ---------------------------------------------------------------------------
async def _type_map(db: DBSessionDep, company_id: uuid.UUID) -> dict[uuid.UUID, LeaveType]:
    rows = await leave_service.list_types(db, company_id)
    return {t.id: t for t in rows}


def _request_out(req, types: dict[uuid.UUID, LeaveType], emp_name: str | None) -> LeaveRequestOut:
    lt = types.get(req.leave_type_id)
    return LeaveRequestOut(
        **LeaveRequestOut.model_validate(req).model_dump(
            exclude={"employee_name", "leave_type_name", "leave_type_code"}
        ),
        employee_name=emp_name,
        leave_type_name=lt.name if lt else None,
        leave_type_code=lt.code if lt else None,
    )


@router.get("/leave/types", response_model=list[LeaveTypeOut])
async def my_leave_types(
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: uuid.UUID = Depends(get_current_employee_id),
) -> list[LeaveType]:
    """Active leave types — so the apply form can offer the right options.

    If the company has no leave types yet, seed the standard defaults
    (CL/SL/EL/ML/PL/BL/LOP) so an employee always has something to request;
    admins can edit/disable them afterwards in Payroll → Leave.
    """
    types = await leave_service.list_types(db, company_id, active_only=True)
    if not types:
        await leave_service.seed_default_types(db, company_id)
        types = await leave_service.list_types(db, company_id, active_only=True)
    return types


@router.get("/leave/balances", response_model=list[LeaveBalanceOut])
async def my_leave_balances(
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    employee_id: uuid.UUID = Depends(get_current_employee_id),
) -> list[LeaveBalanceOut]:
    rows = await leave_service.list_balances(db, company_id, DEFAULT_FINANCIAL_YEAR, employee_id=employee_id)
    types = await _type_map(db, company_id)
    out: list[LeaveBalanceOut] = []
    for b in rows:
        lt = types.get(b.leave_type_id)
        out.append(
            LeaveBalanceOut(
                **LeaveBalanceOut.model_validate(b).model_dump(
                    exclude={"balance", "employee_name", "leave_type_name", "leave_type_code", "is_paid"}
                ),
                balance=Decimal(str(b.accrued or "0")) - Decimal(str(b.used or "0")),
                leave_type_name=lt.name if lt else None,
                leave_type_code=lt.code if lt else None,
                is_paid=lt.is_paid if lt else None,
            )
        )
    return out


@router.get("/leave/requests", response_model=list[LeaveRequestOut])
async def my_leave_requests(
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    employee_id: uuid.UUID = Depends(get_current_employee_id),
) -> list[LeaveRequestOut]:
    rows = await leave_service.list_requests(db, company_id, employee_id=employee_id)
    types = await _type_map(db, company_id)
    emp = (await db.execute(select(Employee).where(Employee.id == employee_id))).scalar_one_or_none()
    name, _ = _employee_label(emp)
    return [_request_out(r, types, name) for r in rows]


@router.post("/leave/requests", response_model=LeaveRequestOut, status_code=201)
async def file_my_leave_request(
    payload: MyLeaveRequestIn,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    employee_id: uuid.UUID = Depends(get_current_employee_id),
    current_user: User = Depends(require_permission(Permission.SELF_READ)),
) -> LeaveRequestOut:
    """File a leave request for oneself. employee_id is forced from the link, so
    a user can never file leave on another employee's behalf."""
    # Read user fields BEFORE the service commit — the commit expires the ORM
    # instance and a later attribute access would trigger a sync lazy-load
    # (MissingGreenlet) in this async context.
    actor_id = current_user.id
    actor_name = current_user.full_name or current_user.email
    full = LeaveRequestIn(
        employee_id=employee_id,
        leave_type_id=payload.leave_type_id,
        start_date=payload.start_date,
        end_date=payload.end_date,
        half_day=payload.half_day,
        reason=payload.reason,
    )
    req = await leave_service.create_request(db, company_id, full, actor_id)
    types = await _type_map(db, company_id)
    return _request_out(req, types, actor_name)


@router.post("/leave/requests/{request_id}/cancel", response_model=LeaveRequestOut)
async def cancel_my_leave_request(
    request_id: uuid.UUID,
    payload: LeaveDecisionIn,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    employee_id: uuid.UUID = Depends(get_current_employee_id),
    current_user: User = Depends(require_permission(Permission.SELF_READ)),
) -> LeaveRequestOut:
    """Cancel one's own leave request (404 if not theirs). Restores balance and
    resyncs the timesheet exactly like the HR-side cancel."""
    # Capture user fields before the service commit (see file_my_leave_request).
    actor_id = current_user.id
    actor_name = current_user.full_name or current_user.email
    await leave_service.get_request_for_employee(db, company_id, request_id, employee_id)
    req = await leave_service.cancel_request(db, company_id, request_id, actor_id, payload.note)
    await timesheet_service.resync_leave_for_employee_period(
        db, company_id, req.employee_id, req.start_date, req.end_date
    )
    await db.refresh(req)
    types = await _type_map(db, company_id)
    return _request_out(req, types, actor_name)


# ---------------------------------------------------------------------------
# 360 feedback — assessments where the signed-in employee is the RATER.
# Replaces the old public /x360/portal/* endpoints: the rater identity comes
# from the login token (get_current_employee_id), never a URL, so a user can
# only ever see/submit their OWN assignments.
# ---------------------------------------------------------------------------
async def _owned_360_assignment(
    db: DBSessionDep, assignment_id: uuid.UUID, employee_id: uuid.UUID, *, with_questions: bool = False
) -> X360AssessmentAssignment:
    """Load a 360 assignment only if the signed-in employee is its rater.

    A mismatch raises 404 (not 403) so a user can't probe which assignment ids
    exist for other raters."""
    opts: list = [selectinload(X360AssessmentAssignment.ratee), selectinload(X360AssessmentAssignment.cycle)]
    if with_questions:
        opts.append(
            selectinload(X360AssessmentAssignment.cycle)
            .selectinload(X360AssessmentCycle.template)
            .selectinload(X360AssessmentTemplate.questions)
            .selectinload(X360TemplateQuestion.question)
        )
    assignment = (
        await db.execute(
            select(X360AssessmentAssignment)
            .where(X360AssessmentAssignment.id == assignment_id)
            .options(*opts)
        )
    ).scalar_one_or_none()
    if assignment is None or assignment.rater_id != employee_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found.")
    return assignment


@router.get("/360-assignments")
async def my_360_assignments(
    db: DBSessionDep, employee_id: uuid.UUID = Depends(get_current_employee_id)
) -> list[dict[str, object]]:
    """The signed-in employee's PENDING 360 assignments (as a rater)."""
    rows = (
        (
            await db.execute(
                select(X360AssessmentAssignment)
                .where(
                    X360AssessmentAssignment.rater_id == employee_id,
                    X360AssessmentAssignment.status == AssignmentStatus.PENDING,
                )
                .options(
                    selectinload(X360AssessmentAssignment.ratee), selectinload(X360AssessmentAssignment.cycle)
                )
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(a.id),
            "relation": a.relation,
            "ratee_name": f"{a.ratee.first_name} {a.ratee.last_name}" if a.ratee else "Unknown",
            "cycle_name": a.cycle.name if a.cycle else "",
        }
        for a in rows
    ]


@router.get("/360-assignments/{assignment_id}")
async def my_360_assignment_detail(
    assignment_id: uuid.UUID, db: DBSessionDep, employee_id: uuid.UUID = Depends(get_current_employee_id)
) -> dict[str, object]:
    """Questions for one of the employee's own 360 assignments (404 if not theirs)."""
    assignment = await _owned_360_assignment(db, assignment_id, employee_id, with_questions=True)
    questions: list[dict[str, object]] = []
    if assignment.cycle and assignment.cycle.template:
        for q in assignment.cycle.template.questions:
            questions.append(
                {
                    "id": str(q.question.id),
                    "text": q.question.text,
                    "type": q.question.type,
                    "category": q.question.category,
                }
            )
    return {
        "id": str(assignment.id),
        "ratee_name": (
            f"{assignment.ratee.first_name} {assignment.ratee.last_name}" if assignment.ratee else "Unknown"
        ),
        "relation": assignment.relation,
        "status": assignment.status,
        "questions": questions,
    }


@router.post("/360-assignments/{assignment_id}/submit")
async def submit_my_360_assignment(
    assignment_id: uuid.UUID,
    request: X360AssessmentSubmit,
    db: DBSessionDep,
    employee_id: uuid.UUID = Depends(get_current_employee_id),
) -> dict[str, str]:
    """Submit feedback for one's own 360 assignment. Ownership-checked; a single
    submission only (409 if already completed) so scores can't be double-counted."""
    assignment = await _owned_360_assignment(db, assignment_id, employee_id)
    if assignment.status == AssignmentStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This assessment has already been submitted."
        )
    for resp in request.responses:
        db.add(
            X360AssessmentResponse(
                assignment_id=assignment.id,
                question_id=resp.question_id,
                answer_value=resp.answer_value,
                answer_text=resp.answer_text,
            )
        )
    assignment.status = AssignmentStatus.COMPLETED
    assignment.completed_at = cast("Any", datetime.now(UTC).replace(tzinfo=None))
    await db.commit()
    return {"status": "success"}


# ---------------------------------------------------------------------------
# Surveys — invites addressed to the signed-in employee. Replaces the tokened
# public survey portal for internal staff; identity from the login token.
# ---------------------------------------------------------------------------
async def _owned_survey_invite(
    db: DBSessionDep, invite_id: uuid.UUID, employee_id: uuid.UUID, *, with_questions: bool = False
) -> SurveyInviteModel:
    """Load a survey invite only if it's addressed to the signed-in employee (404 otherwise)."""
    if with_questions:
        opts: list = [
            selectinload(SurveyInviteModel.instance)
            .selectinload(SurveyInstanceModel.template)
            .selectinload(SurveyTemplateModel.questions),
            selectinload(SurveyInviteModel.instance)
            .selectinload(SurveyInstanceModel.template)
            .selectinload(SurveyTemplateModel.survey_type),
        ]
    else:
        opts = [selectinload(SurveyInviteModel.instance).selectinload(SurveyInstanceModel.template)]
    invite = (
        await db.execute(select(SurveyInviteModel).where(SurveyInviteModel.id == invite_id).options(*opts))
    ).scalar_one_or_none()
    if invite is None or invite.employee_id != employee_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Survey invite not found.")
    return invite


@router.get("/survey-invites")
async def my_survey_invites(
    db: DBSessionDep, employee_id: uuid.UUID = Depends(get_current_employee_id)
) -> list[dict[str, object]]:
    """The signed-in employee's PENDING survey invites."""
    rows = (
        (
            await db.execute(
                select(SurveyInviteModel)
                .where(
                    SurveyInviteModel.employee_id == employee_id,
                    SurveyInviteModel.status == SurveyInviteStatus.PENDING,
                )
                .options(selectinload(SurveyInviteModel.instance).selectinload(SurveyInstanceModel.template))
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(i.id),
            "instance_name": i.instance.name if i.instance else "",
            "template_title": (i.instance.template.title if i.instance and i.instance.template else "Survey"),
        }
        for i in rows
    ]


@router.get("/survey-invites/{invite_id}")
async def my_survey_invite_detail(
    invite_id: uuid.UUID, db: DBSessionDep, employee_id: uuid.UUID = Depends(get_current_employee_id)
) -> dict[str, object]:
    """Questions for one of the employee's own survey invites (404 if not theirs)."""
    invite = await _owned_survey_invite(db, invite_id, employee_id, with_questions=True)
    tpl = invite.instance.template if invite.instance else None
    questions: list[dict[str, object]] = []
    if tpl:
        for q in sorted(tpl.questions, key=lambda x: x.order):
            questions.append(
                {
                    "id": str(q.id),
                    "text": q.text,
                    "type": q.type,
                    "scale_min": q.scale_min,
                    "scale_max": q.scale_max,
                    "options": q.options,
                }
            )
    return {
        "id": str(invite.id),
        "status": invite.status,
        "instance_name": invite.instance.name if invite.instance else "",
        "template_title": tpl.title if tpl else "Survey",
        "questions": questions,
    }


@router.post("/survey-invites/{invite_id}/submit")
async def submit_my_survey_invite(
    invite_id: uuid.UUID,
    submission: SurveySubmission,
    db: DBSessionDep,
    employee_id: uuid.UUID = Depends(get_current_employee_id),
) -> dict[str, str]:
    """Submit one's own survey invite. Ownership-checked; single submission only."""
    invite = await _owned_survey_invite(db, invite_id, employee_id)
    if invite.status == SurveyInviteStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="This survey has already been submitted."
        )
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
    invite.completed_at = cast("Any", datetime.now(UTC).replace(tzinfo=None))
    await db.commit()
    return {"message": "Survey submitted successfully"}


# ---------------------------------------------------------------------------
# Skill assessments — timed aptitude/coding tests assigned to the signed-in
# employee by their org. Definition (questions/duration) lives on the reused
# `assessment_templates`; identity comes from the login token so a user can only
# ever see/submit their OWN assignments.
# ---------------------------------------------------------------------------
async def _owned_skill_assignment(
    db: DBSessionDep, assignment_id: uuid.UUID, employee_id: uuid.UUID
) -> SkillAssessmentAssignment:
    assignment = (
        await db.execute(
            select(SkillAssessmentAssignment).where(SkillAssessmentAssignment.id == assignment_id)
        )
    ).scalar_one_or_none()
    if assignment is None or assignment.employee_id != employee_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assessment not found.")
    return assignment


@router.get("/skill-assessments")
async def my_skill_assessments(
    db: DBSessionDep, employee_id: uuid.UUID = Depends(get_current_employee_id)
) -> list[dict[str, object]]:
    """The signed-in employee's assigned skill assessments (pending first)."""
    rows = (
        await db.execute(
            select(SkillAssessmentAssignment, AssessmentTemplate)
            .join(AssessmentTemplate, SkillAssessmentAssignment.template_id == AssessmentTemplate.id)
            .where(SkillAssessmentAssignment.employee_id == employee_id)
            .order_by(SkillAssessmentAssignment.assigned_at.desc())
        )
    ).all()
    return [
        {
            "id": str(a.id),
            "name": t.name,
            "topic": t.topic,
            "type": t.type.value if hasattr(t.type, "value") else str(t.type),
            "duration": t.test_duration,
            "question_count": len(t.generated_questions or []),
            "status": a.status,
            "score": a.score,
        }
        for a, t in rows
    ]


@router.get("/skill-assessments/{assignment_id}")
async def my_skill_assessment_detail(
    assignment_id: uuid.UUID, db: DBSessionDep, employee_id: uuid.UUID = Depends(get_current_employee_id)
) -> dict[str, object]:
    """Questions for one of the employee's own assessments (correct answers stripped)."""
    assignment = await _owned_skill_assignment(db, assignment_id, employee_id)
    template = (
        await db.execute(select(AssessmentTemplate).where(AssessmentTemplate.id == assignment.template_id))
    ).scalar_one_or_none()
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assessment not found.")

    questions = cast("list[dict[str, Any]]", template.generated_questions or [])
    safe = [{k: v for k, v in q.items() if k != "correct_answer"} for q in questions]
    return {
        "id": str(assignment.id),
        "name": template.name,
        "topic": template.topic,
        "type": template.type.value if hasattr(template.type, "value") else str(template.type),
        "duration": template.test_duration,
        "status": assignment.status,
        "questions": safe,
    }


@router.post("/skill-assessments/{assignment_id}/submit")
async def submit_my_skill_assessment(
    assignment_id: uuid.UUID,
    answers: dict[str, Any],
    db: DBSessionDep,
    employee_id: uuid.UUID = Depends(get_current_employee_id),
) -> dict[str, object]:
    """Grade and store one's own assessment. Single submission only (409 if done)."""
    assignment = await _owned_skill_assignment(db, assignment_id, employee_id)
    if assignment.status == "COMPLETED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This assessment has already been submitted."
        )
    template = (
        await db.execute(select(AssessmentTemplate).where(AssessmentTemplate.id == assignment.template_id))
    ).scalar_one_or_none()
    questions = cast("list[dict[str, Any]]", (template.generated_questions if template else []) or [])

    overall, apt, cod = await grade_assessment(questions, answers)
    assignment.answers = answers
    assignment.score = overall
    assignment.aptitude_score = apt
    assignment.coding_score = cod
    assignment.status = "COMPLETED"
    assignment.completed_at = cast("Any", datetime.now(UTC).replace(tzinfo=None))
    await db.commit()
    return {"status": "success", "score": overall}
