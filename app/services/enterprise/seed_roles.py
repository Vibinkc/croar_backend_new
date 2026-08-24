"""Ensure every company has a set of sensible, assignable custom roles.

Seeded at signup for each new org (see auth.py) AND on startup as a backfill (idempotent).
A fresh org otherwise has only the hidden system ADMIN role, so the team "Assign Roles"
dropdown is empty and you can't onboard anyone with a role.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger
from sqlalchemy import select

from app.core.database import db_manager
from app.models.enterprise.company import Company
from app.models.shared.auth import Permission, Role
from app.models.shared.constants import ModuleScope as M
from app.models.shared.constants import PermissionAction as A

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

# name -> (description, role_rank, [(module, [actions])])
_DEFAULT_ROLES: dict[str, tuple[str, int, list[tuple[M, list[A]]]]] = {
    "Recruiter": (
        "Owns requisitions and manages candidates end-to-end.",
        40,
        [
            (M.jobs, [A.read, A.create, A.update, A.assign, A.publish]),
            (M.candidates, [A.read, A.create, A.update]),
            (M.interviews, [A.read, A.create]),
            (M.assessments, [A.read]),
            (M.onboarding, [A.read]),
            (M.automation, [A.read]),
            (M.analytics, [A.read]),
        ],
    ),
    "Hiring Manager": (
        "Reviews candidates and owns requisitions for their team.",
        45,
        [
            (M.jobs, [A.read, A.assign]),
            (M.candidates, [A.read]),
            (M.interviews, [A.read, A.review]),
            (M.analytics, [A.read]),
        ],
    ),
    "HR Manager": (
        "Manages employees, onboarding and team members.",
        30,
        [
            (M.employees, [A.read, A.create, A.update, A.moderate]),
            (M.onboarding, [A.read, A.create, A.update]),
            (M.jobs, [A.read]),
            (M.payroll, [A.read]),
            (M.analytics, [A.read]),
        ],
    ),
}


async def seed_roles_for_company(s: AsyncSession, company_id: UUID | str) -> int:
    """Add any missing default roles for one company using the caller's session.

    Does NOT commit — participates in the caller's transaction (e.g. signup). Returns the
    number of roles created. Idempotent: skips roles the company already has by name.
    """
    existing = set((await s.execute(select(Role.name).where(Role.tenant_id == company_id))).scalars().all())
    missing = {n: v for n, v in _DEFAULT_ROLES.items() if n not in existing}
    if not missing:
        return 0

    # (module, action) -> a system permission (tenant_id NULL) to link into roles.
    perms = (await s.execute(select(Permission).where(Permission.tenant_id.is_(None)))).scalars().all()
    pmap: dict[tuple[M, A], Permission] = {}
    for p in perms:
        pmap.setdefault((p.module, p.action), p)

    created = 0
    for name, (desc, rank, matrix) in missing.items():
        role = Role(name=name, description=desc, is_system=False, tenant_id=company_id, role_rank=rank)
        for module, actions in matrix:
            for action in actions:
                perm = pmap.get((module, action))
                if perm is not None:
                    role.permissions.append(perm)
        s.add(role)
        created += 1
    return created


async def seed_default_org_roles() -> None:
    """Backfill default roles for every company missing them (idempotent, best-effort).

    Runs on startup. New companies are seeded inline at signup; this catches any that
    predate the signup-time seeding or were created while the app was down.
    """
    try:
        async with db_manager.session() as s:
            company_ids = (await s.execute(select(Company.id))).scalars().all()
            if not company_ids:
                return
            created = 0
            for cid in company_ids:
                created += await seed_roles_for_company(s, cid)
            if created:
                await s.commit()
                logger.info(f"Seeded {created} default org role(s) across {len(company_ids)} companies.")
    except Exception as exc:  # never block startup on seeding
        logger.warning(f"Default org-role seeding skipped: {exc}")
