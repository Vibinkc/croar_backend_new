"""Dashboard overview — numbers that change what you do next.

The previous /stats endpoint returned five totals, and the page built a donut out of four of
them. That donut summed candidates, applications, interviews and AI matches into one figure,
which is not a quantity of anything: those are different entities at different stages, and one
candidate can be several applications. A total of them means nothing.

It also reported "Active Jobs" as a count of every job that was not deleted, ignoring status
entirely, so drafts and closed requisitions inflated it.

This replaces both with three honest groups:

  * **queues** — work waiting on a person, each one a thing you can go and clear. These lead
    the response because they are the only numbers that change what you do today.
  * **pipeline** — where live applications stand right now, as a share of those still in
    play. Deliberately not a funnel: status_id is a current state, so the stages are
    disjoint buckets and a stage-to-stage conversion would be meaningless.
  * **workforce** and **payroll** — who you have, and whether this month can actually be paid.

Every count is scoped to the caller's company and excludes soft-deleted rows. Anything the
caller lacks permission for is omitted rather than returned as zero, because a zero reads as
"nothing to do" and that is a different claim from "you cannot see this".
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.asset import AssetAssignment
from app.models.enterprise.attendance import AttendanceRegularization
from app.models.enterprise.candidate import Candidate, CandidateApplication
from app.models.enterprise.employee import Employee
from app.models.enterprise.interview import InterviewSchedule
from app.models.enterprise.job import JobRequirement, JobStatus
from app.models.enterprise.project import ProjectTask
from app.models.payroll import PayrollCycle, Payslip, SalaryStructure
from app.models.payroll.calendar import Holiday
from app.models.payroll.leave import LeaveRequest
from app.models.shared.constants import ModuleScope, PermissionAction

router = APIRouter(prefix="/dashboard", tags=["Enterprise Dashboard"])

# The stages an application can sit in while it is still live, in order. Ids are stable in the
# seed; the names are what the UI shows.
OPEN_STAGES: list[tuple[int, str]] = [(1, "Applied"), (2, "Screening"), (3, "Interviewing"), (4, "Offered")]
OPEN_STATUS_IDS = tuple(sid for sid, _ in OPEN_STAGES)

# Outcomes. Hired is a result, not a stage anyone waits in, so it is reported alongside the
# other two endings rather than as the tail of a bar chart.
HIRED_ID, REJECTED_ID, WITHDRAWN_ID = 5, 6, 7

# How far ahead the dated agenda looks. Long enough to plan a probation decision or an
# asset hand-back, short enough that the list stays readable.
UPCOMING_WINDOW_DAYS = 30


def _today() -> date:
    return datetime.now(UTC).date()


def _now() -> datetime:
    """Naive UTC — the timestamp columns are TIMESTAMP WITHOUT TIME ZONE."""
    return datetime.now(UTC).replace(tzinfo=None)


async def _count(session: AsyncSession, stmt: Select[Any]) -> int:
    return (await session.execute(stmt)).scalar() or 0


def _ownerless_hint(total: int, stale: int, oldest_days: Any) -> str:
    """Say how long the unowned requisitions have been drifting.

    A requisition with no owner has nobody accountable for moving it, which is usually the
    reason nothing happens on it. Age is what turns that from an admin gap into a real one.
    """
    if total == 0:
        return "Every live requisition has an owner."
    days = int(oldest_days) if oldest_days is not None else 0
    if stale:
        return (
            f"Nobody is accountable for moving {'it' if total == 1 else 'them'} forward. "
            f"{stale} {'has' if stale == 1 else 'have'} been open more than 30 days, the "
            f"oldest {days}. Assign an owner and the rest usually follows."
        )
    return "Nobody is accountable for these yet, but none are older than 30 days. Assign owners now."


def _unmatched_hint(total: int, stale: int, oldest_days: Any) -> str:
    """Same idea for candidates sitting in the pool attached to nothing."""
    if total == 0:
        return "Every candidate in the pool is attached to at least one role."
    days = int(oldest_days) if oldest_days is not None else 0
    tail = (
        f" {stale} {'has' if stale == 1 else 'have'} waited more than 30 days, the longest {days}."
        if stale
        else f" The longest has waited {days} days."
    )
    return (
        "Sourced into the pool and attached to no role, so they appear on no pipeline board "
        "and nothing else surfaces them." + tail
    )


async def _pipeline_for(
    session: AsyncSession, company_id: Any, job_id: uuid.UUID | None = None
) -> dict[str, Any]:
    """Where applications stand, optionally narrowed to one requisition.

    Shared by the overview and the job-filtered endpoint so the two can never disagree about
    what a stage means. Passing ``job_id`` adds one predicate and changes nothing else.
    """
    where = [CandidateApplication.company_id == company_id, CandidateApplication.deleted_at.is_(None)]
    if job_id is not None:
        where.append(CandidateApplication.job_requirement_id == job_id)

    rows = (
        await session.execute(
            select(CandidateApplication.status_id, func.count(CandidateApplication.id))
            .where(*where)
            .group_by(CandidateApplication.status_id)
        )
    ).all()
    by_status = {int(sid): int(n) for sid, n in rows}

    total = sum(by_status.values())
    hired = by_status.get(HIRED_ID, 0)
    in_play = sum(by_status.get(sid, 0) for sid in OPEN_STATUS_IDS)

    # Deliberately NOT a funnel, and not labelled as one.
    #
    # `status_id` is the application's *current* status, so these stages are mutually exclusive
    # buckets: somebody at Offered is no longer counted in Applied. A stage-to-stage conversion
    # would be dividing disjoint sets, which is a number with no meaning. There is also no
    # stage-change history anywhere in the schema, and a rejection overwrites the stage
    # entirely — a candidate rejected at interview becomes simply "Rejected", so how far they
    # actually got is unrecoverable.
    #
    # What a snapshot *can* honestly say is where everyone stands right now, and each stage's
    # share of the applications still in play. Those are reported instead.
    return {
        "stages": [
            {
                "stage": name,
                "count": by_status.get(sid, 0),
                "share": round(by_status.get(sid, 0) / in_play * 100) if in_play else 0,
            }
            for sid, name in OPEN_STAGES
        ],
        "in_play": in_play,
        "total_applications": total,
        "hired": hired,
        "rejected": by_status.get(REJECTED_ID, 0),
        "withdrawn": by_status.get(WITHDRAWN_ID, 0),
        # Hire rate IS computable from a snapshot: of every application ever received, the
        # proportion that ended in a hire. It needs no stage history.
        "hire_rate": round(hired / total * 100, 1) if total else 0.0,
        "job_id": str(job_id) if job_id else None,
    }


def _effective_permissions(user: object) -> set[str]:
    """Every "<module>:<action>" the caller holds, as strings.

    Built the same way PermissionChecker decides a single check: the user's own roles plus the
    roles of every group they belong to, skipping deleted groups. A union, never a subtraction.

    This is assembled here rather than read off the user because there is no flat permission
    list on the model — permissions hang off roles as module/action pairs. Reading a
    non-existent ``user.permissions`` silently yielded an empty set, which made every section
    of this response disappear for a full administrator.

    A SuperAdmin has no per-module rows at all and is granted everything via "*".
    """
    if getattr(user, "roles", None) is None:
        return {"*"}

    roles = list(getattr(user, "roles", None) or [])
    for group in getattr(user, "groups", None) or []:
        if getattr(group, "deleted_at", None) is None:
            roles.extend(getattr(group, "roles", None) or [])

    granted: set[str] = set()
    for role in roles:
        if str(getattr(role, "name", "")).upper() == "SUPER_ADMIN":
            return {"*"}
        for perm in getattr(role, "permissions", None) or []:
            granted.add(f"{perm.module}:{perm.action}")
    return granted


@router.get("/overview")
async def dashboard_overview(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.read))
    ],
) -> dict[str, Any]:
    """Queues, funnel, workforce and payroll health for the signed-in company."""
    company_id = getattr(current_user, "company_id", None)
    granted = _effective_permissions(current_user)

    def allowed(perm: str) -> bool:
        return "*" in granted or perm in granted

    today = _today()
    week_ahead = today + timedelta(days=7)

    app_scope = and_(CandidateApplication.company_id == company_id, CandidateApplication.deleted_at.is_(None))

    out: dict[str, Any] = {
        "as_of": datetime.now(UTC).isoformat(),
        "queues": [],
        "pipeline": None,
        "workforce": None,
        "payroll": None,
    }

    # ---------------------------------------------------------------- queues
    # Each entry is something a person has to act on, with somewhere to go and do it.
    if allowed("jobs:read"):
        # Live requisitions with nobody accountable for them.
        #
        # An unowned role is the usual reason a role goes quiet: no single person is on the hook
        # for sourcing, scheduling or chasing, so it drifts. Scoped to Active/Open because a
        # draft with no owner is just an unfinished draft.
        ownerless = (
            select(JobRequirement.id, JobRequirement.created_at)
            .join(JobStatus, JobStatus.id == JobRequirement.status_id)
            .where(
                JobRequirement.company_id == company_id,
                JobRequirement.deleted_at.is_(None),
                func.lower(JobStatus.name).in_(("active", "open")),
                JobRequirement.owner_id.is_(None),
            )
            .subquery()
        )
        ownerless_total, ownerless_stale, ownerless_oldest = (
            await session.execute(
                select(
                    func.count(ownerless.c.id),
                    func.count(ownerless.c.id).filter(ownerless.c.created_at < _now() - timedelta(days=30)),
                    func.max(func.extract("day", _now() - ownerless.c.created_at)),
                )
            )
        ).one()

        out["queues"].append(
            {
                "key": "jobs_without_owner",
                "label": "Live roles with no owner",
                "count": int(ownerless_total or 0),
                "href": "/enterprise/jobs",
                "hint": _ownerless_hint(
                    int(ownerless_total or 0), int(ownerless_stale or 0), ownerless_oldest
                ),
                "tone": "warning",
            }
        )

    if allowed("candidates:read"):
        # Strong match, still in play. The previous version counted these without checking
        # whether anyone had already acted, so a hired candidate kept being "recommended".
        strong = await _count(
            session,
            select(func.count(CandidateApplication.id)).where(
                app_scope,
                CandidateApplication.ai_match_score >= 80,
                CandidateApplication.status_id.in_(OPEN_STATUS_IDS),
            ),
        )
        out["queues"].append(
            {
                "key": "strong_matches",
                "label": "Strong matches waiting",
                "count": strong,
                "href": "/enterprise/candidates/kanban",
                "hint": "Scored 80 or above by the match engine and not yet decided.",
                "tone": "success",
            }
        )

        offers = await _count(
            session,
            select(func.count(CandidateApplication.id)).where(app_scope, CandidateApplication.status_id == 4),
        )
        out["queues"].append(
            {
                "key": "offers_out",
                "label": "Offers awaiting a reply",
                "count": offers,
                "href": "/enterprise/candidates/kanban",
                "hint": "Offered, and neither accepted nor declined.",
                "tone": "warning",
            }
        )

        # Joined to the application so an interview left behind by a deleted job is excluded.
        interviews_week = await _count(
            session,
            select(func.count(InterviewSchedule.id))
            .join(CandidateApplication, InterviewSchedule.application_id == CandidateApplication.id)
            .where(
                app_scope,
                InterviewSchedule.status == "SCHEDULED",
                func.date(InterviewSchedule.scheduled_time) >= today,
                func.date(InterviewSchedule.scheduled_time) <= week_ahead,
            ),
        )
        out["queues"].append(
            {
                "key": "interviews_week",
                "label": "Interviews in the next 7 days",
                "count": interviews_week,
                "href": "/enterprise/interviews",
                "hint": "Scheduled and still ahead of us.",
                "tone": "info",
            }
        )

        # Sourced, and then never matched to anything.
        #
        # This is the other half of "open roles with no applicants": candidates sitting in the
        # pool while requisitions sit empty. Neither number alone shows the mismatch, and a
        # candidate with no application appears on no pipeline, so nothing else surfaces them.
        unmatched_pool = (
            select(Candidate.id, Candidate.created_at)
            .where(
                Candidate.company_id == company_id,
                Candidate.deleted_at.is_(None),
                ~select(CandidateApplication.id)
                .where(
                    CandidateApplication.candidate_id == Candidate.id,
                    CandidateApplication.deleted_at.is_(None),
                )
                .exists(),
            )
            .subquery()
        )
        unmatched, unmatched_stale, oldest_candidate_days = (
            await session.execute(
                select(
                    func.count(unmatched_pool.c.id),
                    func.count(unmatched_pool.c.id).filter(
                        unmatched_pool.c.created_at < _now() - timedelta(days=30)
                    ),
                    func.max(func.extract("day", _now() - unmatched_pool.c.created_at)),
                )
            )
        ).one()

        out["queues"].append(
            {
                "key": "candidates_unmatched",
                "label": "Candidates never put forward",
                "count": int(unmatched or 0),
                "href": "/enterprise/candidates",
                "hint": _unmatched_hint(
                    int(unmatched or 0), int(unmatched_stale or 0), oldest_candidate_days
                ),
                "tone": "info",
            }
        )

    if allowed("employees:read"):
        pending_reg = await _count(
            session,
            select(func.count(AttendanceRegularization.id)).where(
                AttendanceRegularization.company_id == company_id,
                AttendanceRegularization.status == "pending",
            ),
        )
        out["queues"].append(
            {
                "key": "attendance_requests",
                "label": "Attendance corrections to decide",
                "count": pending_reg,
                "href": "/enterprise/attendance",
                "hint": "Approving one is what writes the corrected day. Clear these before payroll.",
                "tone": "warning",
            }
        )

    if allowed("tasks:read"):
        overdue = await _count(
            session,
            select(func.count(ProjectTask.id)).where(
                ProjectTask.company_id == company_id,
                ProjectTask.status.notin_(("Done", "Completed")),
                ProjectTask.due_date.is_not(None),
                ProjectTask.due_date < today,
            ),
        )
        out["queues"].append(
            {
                "key": "tasks_overdue",
                "label": "Tasks past their due date",
                "count": overdue,
                "href": "/enterprise/tasks",
                "hint": "Still open, and the date has gone.",
                "tone": "danger",
            }
        )

    if allowed("payroll:read"):
        pending_leave = await _count(
            session,
            select(func.count(LeaveRequest.id)).where(
                LeaveRequest.company_id == company_id,
                LeaveRequest.status == "PENDING",
                LeaveRequest.deleted_at.is_(None),
            ),
        )
        out["queues"].append(
            {
                "key": "leave_pending",
                "label": "Leave requests to decide",
                "count": pending_leave,
                "href": "/enterprise/payroll/leave",
                "hint": "Unpaid leave becomes loss of pay, so one left pending overpays somebody.",
                "tone": "warning",
            }
        )

        # Cannot be paid: no active salary structure, or no bank account. The same two
        # blocking conditions the Missing Information report uses.
        structured = select(SalaryStructure.employee_id).where(
            SalaryStructure.company_id == company_id,
            SalaryStructure.is_active.is_(True),
            SalaryStructure.deleted_at.is_(None),
        )
        unpayable = await _count(
            session,
            select(func.count(Employee.id)).where(
                Employee.company_id == company_id,
                Employee.deleted_at.is_(None),
                (Employee.id.notin_(structured))
                | (Employee.bank_account_no.is_(None))
                | (func.trim(Employee.bank_account_no) == ""),
            ),
        )
        out["queues"].append(
            {
                "key": "cannot_be_paid",
                "label": "Employees who cannot be paid",
                "count": unpayable,
                "href": "/enterprise/payroll/reports",
                "hint": "No salary structure, or no bank account. Run Missing Information for the list.",
                "tone": "danger",
            }
        )

    # Biggest problems first, but keep zeros in so a cleared queue is visibly clear.
    out["queues"].sort(key=lambda q: (q["count"] == 0, -q["count"]))

    # ---------------------------------------------------------------- funnel
    if allowed("candidates:read"):
        out["pipeline"] = await _pipeline_for(session, company_id)

    # ------------------------------------------------------------- workforce
    if allowed("employees:read"):
        emp_scope = and_(Employee.company_id == company_id, Employee.deleted_at.is_(None))
        headcount = await _count(session, select(func.count(Employee.id)).where(emp_scope))
        on_probation = await _count(
            session,
            select(func.count(Employee.id)).where(
                emp_scope, Employee.probation_end_date.is_not(None), Employee.probation_end_date >= today
            ),
        )
        joined_30 = await _count(
            session,
            select(func.count(Employee.id)).where(
                emp_scope,
                func.coalesce(Employee.date_of_joining, Employee.hire_date).is_not(None),
                func.coalesce(Employee.date_of_joining, Employee.hire_date) >= today - timedelta(days=30),
            ),
        )
        # Assets currently out. Counts assignments, not assets: one person can hold several,
        # and the register allows only one open assignment per asset.
        assets_out = await _count(
            session,
            select(func.count(AssetAssignment.id)).where(
                AssetAssignment.company_id == company_id, AssetAssignment.returned_at.is_(None)
            ),
        )
        out["workforce"] = {
            "headcount": headcount,
            "on_probation": on_probation,
            "joined_last_30_days": joined_30,
            "assets_issued": assets_out,
        }

    # --------------------------------------------------------------- payroll
    if allowed("payroll:read"):
        open_cycle = (
            await session.execute(
                select(PayrollCycle)
                .where(
                    PayrollCycle.company_id == company_id,
                    PayrollCycle.deleted_at.is_(None),
                    PayrollCycle.status.in_(("DRAFT", "PROCESSING", "APPROVED")),
                )
                .order_by(PayrollCycle.pay_date.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        last_paid = (
            await session.execute(
                select(PayrollCycle)
                .where(
                    PayrollCycle.company_id == company_id,
                    PayrollCycle.deleted_at.is_(None),
                    PayrollCycle.status == "PAID",
                )
                .order_by(PayrollCycle.pay_date.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        def cycle_out(c: PayrollCycle | None) -> dict[str, Any] | None:
            if c is None:
                return None
            totals = c.totals or {}
            return {
                "id": str(c.id),
                "name": c.name,
                "status": c.status,
                "pay_date": c.pay_date.isoformat(),
                "headcount": int(totals.get("headcount", 0) or 0),
                "net": float(totals.get("net", 0) or 0),
                # Who the last run left out, straight off the cycle.
                "skipped": len(totals.get("skipped") or []),
            }

        currency = (
            await session.execute(
                select(SalaryStructure.currency)
                .where(SalaryStructure.company_id == company_id, SalaryStructure.deleted_at.is_(None))
                .limit(1)
            )
        ).scalar_one_or_none() or "INR"

        out["payroll"] = {
            "open_cycle": cycle_out(open_cycle),
            "last_paid_cycle": cycle_out(last_paid),
            "currency": currency,
            "payslips_last_cycle": (
                await _count(
                    session,
                    select(func.count(Payslip.id)).where(
                        Payslip.company_id == company_id, Payslip.cycle_id == last_paid.id
                    ),
                )
                if last_paid
                else 0
            ),
        }

    # ------------------------------------------------------------------ jobs
    if allowed("jobs:read"):
        # Gated on the status NAME, not status_id: the ids differ across seeds, so an id
        # comparison silently counts the wrong bucket. The old tile skipped status entirely
        # and reported every non-deleted requisition as "active".
        active_jobs = await _count(
            session,
            select(func.count(JobRequirement.id))
            .join(JobStatus, JobStatus.id == JobRequirement.status_id)
            .where(
                JobRequirement.company_id == company_id,
                JobRequirement.deleted_at.is_(None),
                func.lower(JobStatus.name).in_(("active", "open")),
            ),
        )
        total_jobs = await _count(
            session,
            select(func.count(JobRequirement.id)).where(
                JobRequirement.company_id == company_id, JobRequirement.deleted_at.is_(None)
            ),
        )
        candidates = await _count(
            session,
            select(func.count(Candidate.id)).where(
                Candidate.company_id == company_id, Candidate.deleted_at.is_(None)
            ),
        )
        # Applications received in the last 30 days against the 30 before, which is the only
        # way to tell a pipeline that is filling from one that has quietly dried up. A count
        # alone cannot distinguish the two.
        apps_30 = await _count(
            session,
            select(func.count(CandidateApplication.id)).where(
                app_scope,
                CandidateApplication.applied_at.is_not(None),
                CandidateApplication.applied_at >= _now() - timedelta(days=30),
            ),
        )
        apps_prev_30 = await _count(
            session,
            select(func.count(CandidateApplication.id)).where(
                app_scope,
                CandidateApplication.applied_at.is_not(None),
                CandidateApplication.applied_at >= _now() - timedelta(days=60),
                CandidateApplication.applied_at < _now() - timedelta(days=30),
            ),
        )
        out["hiring"] = {
            "active_jobs": active_jobs,
            "total_jobs": total_jobs,
            "candidates": candidates,
            "applications_30d": apps_30,
            "applications_prev_30d": apps_prev_30,
        }

        # Options for the pipeline's job filter, each carrying its own application count.
        #
        # The count is the point: most requisitions in a real account have nobody applied, so a
        # bare list of titles makes you open them one at a time to find out which are worth
        # looking at. Ordered by applications so the ones with something to show come first.
        app_count = (
            select(
                CandidateApplication.job_requirement_id.label("job_id"),
                func.count(CandidateApplication.id).label("n"),
            )
            .where(CandidateApplication.company_id == company_id, CandidateApplication.deleted_at.is_(None))
            .group_by(CandidateApplication.job_requirement_id)
            .subquery()
        )
        job_rows = (
            await session.execute(
                select(
                    JobRequirement.id, JobRequirement.title, JobStatus.name, func.coalesce(app_count.c.n, 0)
                )
                .join(JobStatus, JobStatus.id == JobRequirement.status_id)
                .outerjoin(app_count, app_count.c.job_id == JobRequirement.id)
                .where(JobRequirement.company_id == company_id, JobRequirement.deleted_at.is_(None))
                .order_by(func.coalesce(app_count.c.n, 0).desc(), JobRequirement.title)
            )
        ).all()
        out["job_options"] = [
            {"id": str(jid), "title": title, "status": status, "applications": int(n)}
            for jid, title, status, n in job_rows
        ]

    # ------------------------------------------------------------- upcoming
    # A dated agenda of the next few weeks, merged from every module and sorted by date.
    #
    # Nothing else on the dashboard answers "what is coming". The queues say what is late and
    # the counts say what exists, but a probation decision or a public holiday only matters
    # ahead of time — once the date passes, knowing about it is useless.
    upcoming: list[dict[str, Any]] = []

    if allowed("payroll:read"):
        next_holiday = (
            await session.execute(
                select(Holiday.holiday_date, Holiday.name)
                .where(
                    Holiday.company_id == company_id,
                    Holiday.deleted_at.is_(None),
                    Holiday.holiday_date >= today,
                )
                .order_by(Holiday.holiday_date)
                .limit(2)
            )
        ).all()
        for hdate, hname in next_holiday:
            upcoming.append(
                {
                    "date": hdate.isoformat(),
                    "days_away": (hdate - today).days,
                    "label": hname,
                    "detail": "Public holiday. Excluded from working days, so it affects pro-rating.",
                    "kind": "holiday",
                    "href": "/enterprise/payroll/timesheets",
                }
            )

        if out.get("payroll") and (out["payroll"].get("open_cycle") or {}):
            cyc = out["payroll"]["open_cycle"]
            pay_date = date.fromisoformat(cyc["pay_date"])
            if pay_date >= today:
                upcoming.append(
                    {
                        "date": cyc["pay_date"],
                        "days_away": (pay_date - today).days,
                        "label": f"Pay date — {cyc['name']}",
                        "detail": f"Cycle is {cyc['status']}. Everything before approval has to happen first.",
                        "kind": "payroll",
                        "href": f"/enterprise/payroll/{cyc['id']}",
                    }
                )

    if allowed("employees:read"):
        # Probation decisions are the classic missed deadline: the date passes and the employee
        # is confirmed by silence rather than by anyone deciding.
        probation = (
            await session.execute(
                select(Employee.probation_end_date, Employee.first_name, Employee.last_name)
                .where(
                    Employee.company_id == company_id,
                    Employee.deleted_at.is_(None),
                    Employee.probation_end_date.is_not(None),
                    Employee.probation_end_date >= today,
                    Employee.probation_end_date <= today + timedelta(days=UPCOMING_WINDOW_DAYS),
                )
                .order_by(Employee.probation_end_date)
                .limit(5)
            )
        ).all()
        for pdate, first, last in probation:
            upcoming.append(
                {
                    "date": pdate.isoformat(),
                    "days_away": (pdate - today).days,
                    "label": f"Probation ends — {first} {last}".strip(),
                    "detail": "Needs a confirm-or-extend decision before the date, not after.",
                    "kind": "probation",
                    "href": "/enterprise/employees",
                }
            )

        assets_due = (
            await session.execute(
                select(AssetAssignment.due_back_on, func.count(AssetAssignment.id))
                .where(
                    AssetAssignment.company_id == company_id,
                    AssetAssignment.returned_at.is_(None),
                    AssetAssignment.due_back_on.is_not(None),
                    AssetAssignment.due_back_on >= today,
                    AssetAssignment.due_back_on <= today + timedelta(days=UPCOMING_WINDOW_DAYS),
                )
                .group_by(AssetAssignment.due_back_on)
                .order_by(AssetAssignment.due_back_on)
                .limit(3)
            )
        ).all()
        for ddate, n in assets_due:
            upcoming.append(
                {
                    "date": ddate.isoformat(),
                    "days_away": (ddate - today).days,
                    "label": f"{n} asset{'s' if n != 1 else ''} due back",
                    "detail": "Chase before the date; after it they become the overdue queue above.",
                    "kind": "asset",
                    "href": "/enterprise/assets",
                }
            )

    if allowed("candidates:read"):
        interviews = (
            (
                await session.execute(
                    select(InterviewSchedule.scheduled_time)
                    .join(CandidateApplication, InterviewSchedule.application_id == CandidateApplication.id)
                    .where(
                        app_scope,
                        InterviewSchedule.status == "SCHEDULED",
                        func.date(InterviewSchedule.scheduled_time) >= today,
                        func.date(InterviewSchedule.scheduled_time)
                        <= today + timedelta(days=UPCOMING_WINDOW_DAYS),
                    )
                    .order_by(InterviewSchedule.scheduled_time)
                    .limit(4)
                )
            )
            .scalars()
            .all()
        )
        for when in interviews:
            d = when.date()
            upcoming.append(
                {
                    "date": d.isoformat(),
                    "days_away": (d - today).days,
                    "label": f"Interview at {when.strftime('%H:%M')}",
                    "detail": "Scheduled and confirmed. Make sure the panel knows.",
                    "kind": "interview",
                    "href": "/enterprise/interviews",
                }
            )

    upcoming.sort(key=lambda e: e["date"])
    out["upcoming"] = upcoming[:8]
    out["upcoming_window_days"] = UPCOMING_WINDOW_DAYS

    return out


@router.get("/pipeline")
async def dashboard_pipeline(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
    job_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """The pipeline alone, for one requisition or the whole company.

    Separate from /overview rather than a query parameter on it, because narrowing the overview
    by job would also narrow the queues, the workforce counts and payroll — none of which belong
    to a single requisition. Filtering the card should change the card.

    An unknown or out-of-company job_id returns zeros rather than 404: the filter is a view, and
    a job that no longer exists legitimately has no applications.
    """
    company_id = getattr(current_user, "company_id", None)
    return await _pipeline_for(session, company_id, job_id)
