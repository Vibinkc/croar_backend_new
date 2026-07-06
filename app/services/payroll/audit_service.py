"""Audit trail: derive a human-readable action from a request and persist it.

Used by the audit middleware (app/main.py) to record one row per authenticated
mutating request, and by the audit router to read recent activity back.
"""

import re
import uuid

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import DBSessionManager
from app.models.enterprise.user_role import EnterpriseUser as User
from app.models.payroll.audit import AuditLog

# (method, compiled path pattern, label). First match wins; patterns use the
# API path. UUID segments are matched loosely so any id maps to the same action.
_ID = r"[^/]+"
_RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("POST", re.compile(r"^/api/v1/auth/signup$"), "Registered a new organization"),
    ("POST", re.compile(r"^/api/v1/auth/users$"), "Created a user"),
    ("PUT", re.compile(r"^/api/v1/enterprise/settings/organization$"), "Updated organisation profile"),
    ("PUT", re.compile(rf"^/api/v1/enterprise/taxes/profiles/{_ID}$"), "Updated an IT declaration"),
    ("POST", re.compile(r"^/api/v1/enterprise/taxes/challans$"), "Recorded a TDS challan"),
    ("DELETE", re.compile(rf"^/api/v1/enterprise/taxes/challans/{_ID}$"), "Deleted a TDS challan"),
    ("POST", re.compile(r"^/api/v1/enterprise/payroll/structures$"), "Created salary structure"),
    ("PUT", re.compile(rf"^/api/v1/enterprise/payroll/structures/{_ID}$"), "Updated salary structure"),
    ("DELETE", re.compile(rf"^/api/v1/enterprise/payroll/structures/{_ID}$"), "Deleted salary structure"),
    ("POST", re.compile(r"^/api/v1/enterprise/payroll/cycles$"), "Created payroll cycle"),
    ("POST", re.compile(rf"^/api/v1/enterprise/payroll/cycles/{_ID}/run$"), "Ran payroll cycle"),
    ("POST", re.compile(rf"^/api/v1/enterprise/payroll/cycles/{_ID}/approve$"), "Approved payroll cycle"),
    ("POST", re.compile(rf"^/api/v1/enterprise/payroll/cycles/{_ID}/mark-paid$"), "Marked cycle as paid"),
    ("POST", re.compile(rf"^/api/v1/enterprise/payroll/cycles/{_ID}/cancel$"), "Cancelled payroll cycle"),
    ("DELETE", re.compile(rf"^/api/v1/enterprise/payroll/cycles/{_ID}$"), "Deleted payroll cycle"),
    ("POST", re.compile(rf"^/api/v1/enterprise/payroll/cycles/{_ID}/adjustments$"), "Added a pay adjustment"),
    ("DELETE", re.compile(rf"^/api/v1/enterprise/payroll/adjustments/{_ID}$"), "Removed a pay adjustment"),
    ("POST", re.compile(rf"^/api/v1/enterprise/payroll/payslips/{_ID}/email$"), "Emailed a payslip"),
    (
        "POST",
        re.compile(rf"^/api/v1/enterprise/payroll/cycles/{_ID}/email-payslips$"),
        "Emailed cycle payslips",
    ),
    # Salary templates
    ("POST", re.compile(r"^/api/v1/enterprise/payroll/templates$"), "Created salary template"),
    ("PUT", re.compile(rf"^/api/v1/enterprise/payroll/templates/{_ID}$"), "Updated salary template"),
    ("DELETE", re.compile(rf"^/api/v1/enterprise/payroll/templates/{_ID}$"), "Deleted salary template"),
    ("POST", re.compile(rf"^/api/v1/enterprise/payroll/templates/{_ID}/apply$"), "Applied salary template"),
    # Leave
    ("POST", re.compile(r"^/api/v1/enterprise/leave/types$"), "Created a leave type"),
    ("POST", re.compile(r"^/api/v1/enterprise/leave/types/seed-defaults$"), "Seeded default leave types"),
    ("PUT", re.compile(rf"^/api/v1/enterprise/leave/types/{_ID}$"), "Updated a leave type"),
    ("POST", re.compile(r"^/api/v1/enterprise/leave/requests$"), "Filed a leave request"),
    ("POST", re.compile(rf"^/api/v1/enterprise/leave/requests/{_ID}/approve$"), "Approved a leave request"),
    ("POST", re.compile(rf"^/api/v1/enterprise/leave/requests/{_ID}/reject$"), "Rejected a leave request"),
    ("POST", re.compile(rf"^/api/v1/enterprise/leave/requests/{_ID}/cancel$"), "Cancelled a leave request"),
    # Timesheets
    ("POST", re.compile(rf"^/api/v1/enterprise/timesheets/cycles/{_ID}/generate$"), "Generated timesheets"),
    ("POST", re.compile(rf"^/api/v1/enterprise/timesheets/cycles/{_ID}/import$"), "Imported attendance"),
    ("PUT", re.compile(rf"^/api/v1/enterprise/timesheets/{_ID}/entries$"), "Updated a timesheet"),
    ("POST", re.compile(rf"^/api/v1/enterprise/timesheets/{_ID}/submit$"), "Submitted a timesheet"),
    ("POST", re.compile(rf"^/api/v1/enterprise/timesheets/{_ID}/approve$"), "Approved a timesheet"),
    ("POST", re.compile(rf"^/api/v1/enterprise/timesheets/{_ID}/reject$"), "Rejected a timesheet"),
    ("POST", re.compile(rf"^/api/v1/enterprise/timesheets/{_ID}/reopen$"), "Reopened a timesheet"),
    # Work calendar
    ("PUT", re.compile(r"^/api/v1/enterprise/calendar/config$"), "Updated work calendar"),
    ("POST", re.compile(r"^/api/v1/enterprise/calendar/holidays$"), "Added a holiday"),
    ("DELETE", re.compile(rf"^/api/v1/enterprise/calendar/holidays/{_ID}$"), "Removed a holiday"),
    # Settings
    ("PUT", re.compile(r"^/api/v1/enterprise/settings/payslip$"), "Updated payslip settings"),
    ("PUT", re.compile(r"^/api/v1/enterprise/settings/payslip/document$"), "Updated payslip template"),
    ("DELETE", re.compile(r"^/api/v1/enterprise/settings/payslip/document$"), "Removed payslip template"),
    ("POST", re.compile(r"^/api/v1/enterprise/settings/payslip/document/scan$"), "Scanned payslip template"),
    ("PUT", re.compile(r"^/api/v1/enterprise/settings/payslip/document/mapping$"), "Mapped payslip template"),
    ("PUT", re.compile(r"^/api/v1/enterprise/settings/statutory$"), "Updated statutory settings"),
    # Employees
    ("POST", re.compile(r"^/api/v1/enterprise/employees$"), "Added an employee"),
    ("PUT", re.compile(rf"^/api/v1/enterprise/employees/{_ID}$"), "Updated an employee"),
    ("DELETE", re.compile(rf"^/api/v1/enterprise/employees/{_ID}$"), "Deleted an employee"),
]


def derive_action(method: str, path: str) -> str:
    for rule_method, pattern, label in _RULES:
        if rule_method == method and pattern.match(path):
            return label
    return f"{method} {path}"


async def record(
    *, company_id: uuid.UUID | None, actor_id: uuid.UUID | None, method: str, path: str, status_code: int
) -> None:
    """Persist one audit row in its own session/transaction (decoupled from the
    request's session). The actor's email is snapshotted here so the trail stays
    readable even if the user is later deleted. Best-effort: never let an audit
    failure break the API."""
    try:
        async with DBSessionManager.session() as session:
            actor_email: str | None = None
            if actor_id is not None:
                # Snapshot the actor's email and (when not already supplied by the
                # token) backfill their company so the trail stays per-tenant.
                row = (
                    await session.execute(select(User.email, User.company_id).where(User.id == actor_id))
                ).first()
                if row is not None:
                    actor_email = row[0]
                    if company_id is None:
                        company_id = row[1]
            session.add(
                AuditLog(
                    company_id=company_id,
                    actor_id=actor_id,
                    actor_email=actor_email,
                    action=derive_action(method, path),
                    method=method,
                    path=path[:255],
                    status_code=status_code,
                )
            )
            await session.commit()
    except Exception as exc:  # pragma: no cover - audit must never break a request
        logger.warning(f"Failed to write audit log for {method} {path}: {exc}")


async def recent(db: AsyncSession, company_id: uuid.UUID, limit: int = 100) -> list[AuditLog]:
    rows = (
        (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.company_id == company_id)
                # Hide read-only live-preview noise that earlier builds recorded
                # before it was excluded from the audit middleware.
                .where(~AuditLog.path.like("%/structures/preview"))
                .order_by(AuditLog.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)
