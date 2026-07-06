"""Employee login provisioning + set-password emails.

Used when a 360 cycle or survey is launched: every participant needs a login so
they can respond from their authenticated ``/employee`` workspace (no public
portal). This creates the EMPLOYEE-role login for anyone who lacks one and emails
them a "set your password" link (reusing the reset-password token + the mail
module). Best-effort: an email failure never blocks the launch.
"""

import secrets
from datetime import timedelta

from fastapi.concurrency import run_in_threadpool
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, get_password_hash
from app.core.settings import get_settings
from app.models.enterprise.employee import Employee
from app.models.enterprise.user_role import EnterpriseUser
from app.models.shared.auth import Role

_settings = get_settings()

# A generous window so onboarding invites don't expire before the employee acts.
_SET_PASSWORD_TTL = timedelta(days=7)


def _set_password_link(email: str) -> str:
    """A reset-password link the employee can use to set their first password.

    Reuses the existing ``reset_password`` token type so ``POST /auth/reset-password``
    accepts it — only the TTL is longer than the forgot-password flow."""
    token = create_access_token(
        subject=email, extra_claims={"type": "reset_password"}, expires_delta=_SET_PASSWORD_TTL
    )
    base = str(_settings.frontend_url).rstrip("/")
    return f"{base}/enterprise/reset-password?token={token}"


async def _send_set_password_email(email: str, name: str, company_name: str | None) -> bool:
    """Send the 'set your password' email via the mail module (threadpool —
    smtplib is blocking). Returns False (logged) on any failure; never raises."""
    link = _set_password_link(email)
    subject = "Set up your Croar employee account"
    body = (
        f"<p>Hi {name or 'there'},</p>"
        f"<p>An account has been created for you so you can complete HR tasks "
        f"(360 feedback, surveys, timesheets, payslips and leave) from your own workspace.</p>"
        f"<p><a href='{link}' "
        f"style='display:inline-block;padding:10px 18px;background:#5B53E0;color:#fff;"
        f"border-radius:8px;text-decoration:none;font-weight:600'>Set your password</a></p>"
        f"<p>Or paste this link into your browser (valid for 7 days):<br>{link}</p>"
        f"<p>Sign in afterwards with <b>{email}</b>.</p>"
    )
    try:
        # Lazy import avoids a router<->service import cycle at load time.
        from app.router.enterprise.communication import send_smtp_email

        ok, err = await run_in_threadpool(
            send_smtp_email, email, subject, body, company_name or _settings.app_name
        )
        if not ok:
            logger.warning(f"Set-password email to {email} failed: {err}")
        return ok
    except Exception as exc:  # pragma: no cover - email must never break provisioning
        logger.warning(f"Set-password email to {email} errored: {exc}")
        return False


async def _get_or_create_employee_role(session: AsyncSession, company_id) -> Role:
    role = (
        await session.execute(select(Role).where(Role.name == "EMPLOYEE", Role.tenant_id == company_id))
    ).scalar_one_or_none()
    if role is None:
        role = Role(
            name="EMPLOYEE",
            description="Employee self-service (own timesheets, payslips, leave, feedback, surveys)",
            tenant_id=company_id,
            is_system=True,
            role_rank=100,
        )
        session.add(role)
        await session.flush()
    return role


async def ensure_logins_for_employees(
    session: AsyncSession, company_id, employee_ids: list, *, company_name: str | None = None
) -> int:
    """Ensure each given employee has an EMPLOYEE-role login; email a set-password
    link to any newly-created account. Returns how many logins were created.

    Idempotent: employees who already have a login (matched by email) are skipped.
    Commits the new users itself (separate from the caller's units of work is not
    required — it flushes/commits here so the launch transaction stays clean)."""
    if not employee_ids:
        return 0

    employees = (
        (
            await session.execute(
                select(Employee).where(
                    Employee.id.in_(employee_ids),
                    Employee.company_id == company_id,
                    Employee.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    emails = [e.email for e in employees if e.email]
    if not emails:
        return 0

    existing = set(
        (await session.execute(select(EnterpriseUser.email).where(EnterpriseUser.email.in_(emails))))
        .scalars()
        .all()
    )

    role: Role | None = None
    to_notify: list[tuple[str, str]] = []  # (email, name)
    for emp in employees:
        if not emp.email or emp.email in existing:
            continue
        if role is None:
            role = await _get_or_create_employee_role(session, company_id)
        user = EnterpriseUser(
            email=emp.email,
            # Random throwaway — the employee sets their own via the emailed link.
            password_hash=get_password_hash(secrets.token_urlsafe(24)),
            first_name=emp.first_name,
            last_name=emp.last_name,
            company_id=company_id,
            is_active=True,
        )
        user.roles = [role]
        session.add(user)
        existing.add(emp.email)  # guard against duplicates within this batch
        to_notify.append((emp.email, f"{emp.first_name} {emp.last_name}".strip() or emp.email))

    if not to_notify:
        return 0

    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    for email, name in to_notify:
        await _send_set_password_email(email, name, company_name)
    return len(to_notify)


account_service_send_reset = _send_set_password_email  # re-export for auth's forgot-password
