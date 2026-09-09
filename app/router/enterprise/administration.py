"""Administration — Manatal's admin hub, and everything it holds.

Their Administration is a hub of ten cards, each opening a sub-list of settings: Account &
Users, Data Management, Integrations, Subscription, Career Page, Job Boards, Resumes,
Customization, Features, Support.

Most of what those cards hold, Croar already had — scattered across a "General" nav group where
nobody looked for them. Those pages now LIVE under /enterprise/administration rather than being
linked out to, so clicking an item goes deeper with a breadcrumb instead of throwing you into a
different part of the sidebar. The old paths redirect.

Two items had no Croar equivalent and are built here for real:

  · Logs — `job_activities` has been recording every publish, edit, candidate add and drop all
    along, and it was only ever visible inside one job. A company-wide view is what makes it an
    audit trail rather than a per-job curiosity.
  · Archive Data — jobs and candidates soft-delete, and until now a soft-deleted row was simply
    invisible with no way back. 48 jobs were sitting in that state in this database. Restore and
    permanent-delete give the state a door at both ends.

Everything Croar genuinely cannot do carries `unavailable` with the reason, the same as the
Reports catalogue. Naming the gap is more useful than hiding the card.
"""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.candidate import Candidate, CandidateApplication
from app.models.enterprise.job import JobActivity, JobRequirement
from app.models.shared.constants import ModuleScope, PermissionAction

router = APIRouter(prefix="/administration", tags=["Administration"])

NO_IMPORT = "Croar has no bulk importer yet. Candidates arrive via CV upload, the apply form, the job inbox or the Sourcing Hub."
NO_GDPR = "Croar does not record a consent flag on a candidate, so there is nothing to track."
NO_GUESTS = "Croar has team members and roles, but no external guest accounts scoped to a department."
NO_SUBSCRIPTION = "Croar meters usage with a credit wallet rather than a subscription, so there are no plans or invoices to manage."
NO_RESUME_BRANDING = "Croar stores the original CV; it does not re-render candidates onto a branded template."
NO_ENRICH = "Profile enrichment runs inside the Sourcing Hub at import time, not as an account-level setting."
NO_REFERRAL = "Croar has no referral scheme, and no referrer field on a candidate."

# Every item Manatal lists, mapped to the Croar page that already does it — or to the reason it
# cannot be done. `href` is a frontend route; the page links straight through.
SECTIONS: list[dict[str, Any]] = [
    {
        "id": "account-and-users",
        "name": "Account & Users",
        "description": "Manage your account details, users, roles and permissions.",
        "icon": "account-cog",
        "items": [
            {
                "name": "Account",
                "description": "Edit your organisation's name, logo and defaults.",
                "href": "/enterprise/administration/account-and-users/account",
            },
            {
                "name": "Users",
                "description": "Manage the people with access to this account.",
                "href": "/enterprise/administration/account-and-users/users",
            },
            {
                "name": "Roles & Permissions",
                "description": "Manage roles and what each one may do.",
                "href": "/enterprise/administration/account-and-users/roles",
            },
            {
                "name": "Partner companies",
                "description": "Client companies this consultancy recruits for.",
                "href": "/enterprise/administration/account-and-users/partners",
            },
            {
                "name": "Guests",
                "description": "External guests scoped to a department.",
                "unavailable": NO_GUESTS,
            },
        ],
    },
    {
        "id": "data-management",
        "name": "Data Management",
        "description": "Audit logs, archived records, imports and consent.",
        "icon": "database-cog",
        "items": [
            {
                "name": "Logs",
                "description": "Every action taken on your jobs and candidates.",
                "href": "/enterprise/administration/logs",
            },
            {
                "name": "Archive Data",
                "description": "Restore or permanently delete archived jobs and candidates.",
                "href": "/enterprise/administration/archive",
            },
            {
                "name": "Data Import",
                "description": "Bulk-import candidates, jobs and more.",
                "unavailable": NO_IMPORT,
            },
            {"name": "GDPR Tracking", "description": "Track candidate consent.", "unavailable": NO_GDPR},
        ],
    },
    {
        "id": "integrations",
        "name": "Integrations",
        "description": "Manage your third-party software and tool integrations.",
        "icon": "puzzle",
        "items": [
            {
                "name": "Integrations",
                "description": "Assessment, interview and mailbox connections.",
                "href": "/enterprise/administration/integrations/tools",
            },
            {
                "name": "Mailboxes",
                "description": "Connect the mailbox sequences send from.",
                "href": "/enterprise/communication",
            },
        ],
    },
    {
        "id": "subscription",
        "name": "Credits & Usage",
        "description": "Your credit wallet and what has been spent.",
        "icon": "wallet",
        "items": [
            {
                "name": "Credits",
                "description": "Wallet balance and the ledger of what used it.",
                "href": "/enterprise/administration/credits/wallet",
            },
            {
                "name": "Plans & Invoices",
                "description": "Subscription tiers and billing history.",
                "unavailable": NO_SUBSCRIPTION,
            },
        ],
    },
    {
        "id": "career-page",
        "name": "Career Page",
        "description": "Enable and set up your career page.",
        "icon": "web",
        "items": [
            {
                "name": "Job Posts",
                "description": "What appears on your public page.",
                "href": "/enterprise/career-page",
            },
            {
                "name": "Career Page Settings",
                "description": "Branding, description and visibility.",
                "href": "/enterprise/career-page/settings",
            },
            {
                "name": "Embed & Share",
                "description": "Embed the board on your own site.",
                "href": "/enterprise/career-page/embed",
            },
        ],
    },
    {
        "id": "job-boards",
        "name": "Job Boards",
        "description": "Manage the boards your jobs are distributed to.",
        "icon": "share-variant",
        "items": [
            {
                "name": "Job Portals",
                "description": "Free boards, feeds and partner-gated boards.",
                "href": "/enterprise/administration/job-boards/portals",
            }
        ],
    },
    {
        "id": "resumes",
        "name": "Resumes",
        "description": "How candidate CVs are stored and presented.",
        "icon": "file-document-outline",
        "items": [
            {
                "name": "Branded resumes",
                "description": "Re-render candidates onto your own template.",
                "unavailable": NO_RESUME_BRANDING,
            }
        ],
    },
    {
        "id": "customization",
        "name": "Customization",
        "description": "Customise jobs, templates and what the dashboard shows.",
        "icon": "tune",
        "items": [
            {
                "name": "Templates",
                "description": "Email, assessment, interview and onboarding templates.",
                "href": "/enterprise/administration/customization/templates",
            },
            {
                "name": "Pipeline stages",
                "description": "Stages are set per job, on the job's own Rounds tab.",
                "href": "/enterprise/jobs",
            },
            {
                "name": "Automation",
                "description": "What fires automatically at each stage.",
                "href": "/enterprise/automation",
            },
        ],
    },
    {
        "id": "features",
        "name": "Features",
        "description": "Duplicate detection, enrichment and other account-level switches.",
        "icon": "toggle-switch",
        "items": [
            {
                "name": "Duplicate detection",
                "description": "Find candidates that look like the same person.",
                "href": "/enterprise/administration/duplicates",
            },
            {
                "name": "Profile enrichment",
                "description": "Fill profiles from public sources.",
                "unavailable": NO_ENRICH,
            },
            {"name": "Referrals", "description": "Employee referral scheme.", "unavailable": NO_REFERRAL},
        ],
    },
    {
        "id": "support",
        "name": "Support",
        "description": "Documentation and how to report a problem.",
        "icon": "lifebuoy",
        "items": [
            {
                "name": "In-app guide",
                "description": "The product guide, openable from any page.",
                "href": "/enterprise/dashboard",
            }
        ],
    },
]


def _company(user: object) -> Any:
    cid = getattr(user, "company_id", None)
    if not cid:
        raise HTTPException(status_code=404, detail="No company on this account.")
    return cid


@router.get("/overview")
async def overview(
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.read))
    ],
) -> dict[str, Any]:
    """The hub: every section, its items, and where each one leads or why it cannot."""
    return {
        "sections": [
            {
                **s,
                "items": [{**i, "available": "unavailable" not in i} for i in s["items"]],
                "available_count": sum(1 for i in s["items"] if "unavailable" not in i),
                "total_count": len(s["items"]),
            }
            for s in SECTIONS
        ]
    }


@router.get("/logs")
async def logs(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.read))
    ],
    q: str | None = None,
    action: str | None = None,
    actor_id: UUID | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """Company-wide audit trail, newest first.

    The rows have existed all along — `job_activities` records every publish, edit, candidate
    add and drop — but were only ever readable one job at a time, which is not an audit trail.
    """
    cid = _company(current_user)
    where = [JobActivity.company_id == cid]
    if action:
        where.append(JobActivity.action == action)
    if actor_id:
        where.append(JobActivity.actor_id == actor_id)
    if q and q.strip():
        like = f"%{q.strip()}%"
        where.append(or_(JobActivity.actor_name.ilike(like), JobRequirement.title.ilike(like)))

    base = (
        select(JobActivity, JobRequirement.title)
        .outerjoin(JobRequirement, JobRequirement.id == JobActivity.job_requirement_id)
        .where(and_(*where))
    )
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        await session.execute(
            base.order_by(JobActivity.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "results": [
            {
                "id": str(a.id),
                "action": a.action,
                "actor_name": a.actor_name,
                "actor_id": str(a.actor_id) if a.actor_id else None,
                "job_id": str(a.job_requirement_id) if a.job_requirement_id else None,
                "job_title": job_title,
                "detail": a.detail or {},
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a, job_title in rows
        ],
    }


@router.get("/logs/filters")
async def log_filters(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.read))
    ],
) -> dict[str, Any]:
    """Actions and actors actually present, so no filter offers a value that matches nothing."""
    cid = _company(current_user)
    actions = (
        await session.execute(
            select(JobActivity.action, func.count(JobActivity.id))
            .where(JobActivity.company_id == cid)
            .group_by(JobActivity.action)
            .order_by(func.count(JobActivity.id).desc())
        )
    ).all()
    actors = (
        await session.execute(
            select(JobActivity.actor_id, JobActivity.actor_name)
            .where(JobActivity.company_id == cid, JobActivity.actor_id.is_not(None))
            .distinct()
        )
    ).all()
    return {
        "actions": [{"action": a, "count": c} for a, c in actions],
        "actors": [{"id": str(i), "name": n or "Unknown"} for i, n in actors if i],
    }


@router.get("/archive")
async def archive(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.read))
    ],
    kind: Literal["jobs", "candidates"] = "jobs",
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
) -> dict[str, Any]:
    """Soft-deleted records, which until now were invisible with no way back."""
    cid = _company(current_user)
    if kind == "jobs":
        where = [JobRequirement.company_id == cid, JobRequirement.deleted_at.is_not(None)]
        total = (
            await session.execute(select(func.count(JobRequirement.id)).where(and_(*where)))
        ).scalar_one()
        rows = (
            (
                await session.execute(
                    select(JobRequirement)
                    .where(and_(*where))
                    .order_by(JobRequirement.deleted_at.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )
        results = [
            {
                "id": str(j.id),
                "label": j.title,
                "sub": j.department or j.location,
                "archived_at": j.deleted_at.isoformat() if j.deleted_at else None,
            }
            for j in rows
        ]
    else:
        where = [Candidate.company_id == cid, Candidate.deleted_at.is_not(None)]
        total = (await session.execute(select(func.count(Candidate.id)).where(and_(*where)))).scalar_one()
        rows = (
            (
                await session.execute(
                    select(Candidate)
                    .where(and_(*where))
                    .order_by(Candidate.deleted_at.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )
        results = [
            {
                "id": str(c.id),
                "label": c.full_name or "Unnamed candidate",
                "sub": c.email,
                "archived_at": c.deleted_at.isoformat() if c.deleted_at else None,
            }
            for c in rows
        ]
    return {"kind": kind, "total": total, "page": page, "page_size": page_size, "results": results}


@router.post("/archive/{kind}/{record_id}/restore")
async def restore(
    kind: Literal["jobs", "candidates"],
    record_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.update))
    ],
) -> dict[str, Any]:
    """Bring a soft-deleted record back by clearing its tombstone."""
    cid = _company(current_user)
    model = JobRequirement if kind == "jobs" else Candidate
    row = (
        await session.execute(
            select(model).where(model.id == record_id, model.company_id == cid, model.deleted_at.is_not(None))
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Not found in the archive.")
    row.deleted_at = None
    await session.commit()
    return {"id": str(record_id), "restored": True}


@router.delete("/archive/{kind}/{record_id}", status_code=204)
async def purge(
    kind: Literal["jobs", "candidates"],
    record_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.delete))
    ],
) -> None:
    """Delete permanently. Only ever reachable for something already archived.

    Requiring the row to be soft-deleted first is the safety: this endpoint cannot be pointed at
    a live job by guessing an id, because a live job is not in the archive.
    """
    cid = _company(current_user)
    model = JobRequirement if kind == "jobs" else Candidate
    row = (
        await session.execute(
            select(model).where(model.id == record_id, model.company_id == cid, model.deleted_at.is_not(None))
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Not found in the archive.")
    await session.delete(row)
    await session.commit()


@router.get("/duplicates")
async def duplicates(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.read))
    ],
) -> dict[str, Any]:
    """Candidates that look like the same person.

    Two rules, kept separate because they carry very different confidence. A shared email is
    effectively proof; a shared name is a prompt to go and look, and the response says which is
    which rather than merging the two into one "duplicates" number.
    """
    cid = _company(current_user)
    scope = [Candidate.company_id == cid, Candidate.deleted_at.is_(None)]

    email_groups = (
        await session.execute(
            select(func.lower(Candidate.email), func.count(Candidate.id))
            .where(*scope, Candidate.email.is_not(None), Candidate.email != "")
            .group_by(func.lower(Candidate.email))
            .having(func.count(Candidate.id) > 1)
        )
    ).all()
    name_groups = (
        await session.execute(
            select(func.lower(Candidate.full_name), func.count(Candidate.id))
            .where(*scope, Candidate.full_name.is_not(None), Candidate.full_name != "")
            .group_by(func.lower(Candidate.full_name))
            .having(func.count(Candidate.id) > 1)
        )
    ).all()

    async def members(field: Any, value: str) -> list[dict[str, Any]]:
        rows = (
            (
                await session.execute(
                    select(Candidate).where(*scope, func.lower(field) == value).order_by(Candidate.created_at)
                )
            )
            .scalars()
            .all()
        )
        out = []
        for c in rows:
            apps = (
                await session.execute(
                    select(func.count(CandidateApplication.id)).where(
                        CandidateApplication.candidate_id == c.id, CandidateApplication.deleted_at.is_(None)
                    )
                )
            ).scalar_one()
            out.append(
                {
                    "id": str(c.id),
                    "full_name": c.full_name,
                    "email": c.email,
                    "source_platform": c.source_platform,
                    "applications": apps,
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                }
            )
        return out

    by_email = [
        {"key": v, "match": "email", "count": n, "candidates": await members(Candidate.email, v)}
        for v, n in email_groups[:50]
    ]
    email_ids = {c["id"] for g in by_email for c in g["candidates"]}
    by_name = []
    for v, n in name_groups[:50]:
        people = await members(Candidate.full_name, v)
        # A pair already caught by email is not a second finding; reporting it twice would
        # inflate the count and send someone to merge the same two people again.
        if all(p["id"] in email_ids for p in people):
            continue
        by_name.append({"key": v, "match": "name", "count": n, "candidates": people})

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "by_email": by_email,
        "by_name": by_name,
        "total_groups": len(by_email) + len(by_name),
    }


@router.post("/duplicates/merge")
async def merge_duplicates(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.update))
    ],
    keep_id: UUID,
    merge_ids: Annotated[list[UUID], Query()],
) -> dict[str, Any]:
    """Move applications onto the kept candidate, then archive the others.

    The duplicates are soft-deleted rather than destroyed: a merge is a judgement call, and one
    made from a name match can be wrong. Archiving leaves it reversible from the Archive screen.
    An application is skipped if the kept candidate is already on that job, since two rows for
    one person on one pipeline is exactly what merging is meant to remove.
    """
    cid = _company(current_user)
    ids = [i for i in merge_ids if i != keep_id]
    if not ids:
        raise HTTPException(status_code=422, detail="Nothing to merge into that candidate.")

    keep = (
        await session.execute(
            select(Candidate).where(
                Candidate.id == keep_id, Candidate.company_id == cid, Candidate.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if keep is None:
        raise HTTPException(status_code=404, detail="Candidate to keep not found.")

    kept_jobs = set(
        (
            await session.execute(
                select(CandidateApplication.job_requirement_id).where(
                    CandidateApplication.candidate_id == keep_id, CandidateApplication.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )

    moved = skipped = archived = 0
    for other_id in ids:
        other = (
            await session.execute(
                select(Candidate).where(
                    Candidate.id == other_id, Candidate.company_id == cid, Candidate.deleted_at.is_(None)
                )
            )
        ).scalar_one_or_none()
        if other is None:
            continue
        apps = (
            (
                await session.execute(
                    select(CandidateApplication).where(
                        CandidateApplication.candidate_id == other_id,
                        CandidateApplication.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for app in apps:
            if app.job_requirement_id in kept_jobs:
                skipped += 1
                continue
            app.candidate_id = keep_id
            kept_jobs.add(app.job_requirement_id)
            moved += 1
        # Fill gaps on the survivor; never overwrite what someone may have corrected by hand.
        if not keep.email and other.email:
            keep.email = other.email
        if not keep.phone and other.phone:
            keep.phone = other.phone
        if not keep.skills and other.skills:
            keep.skills = other.skills
        other.deleted_at = datetime.now(UTC).replace(tzinfo=None)
        archived += 1

    await session.commit()
    return {
        "kept": str(keep_id),
        "applications_moved": moved,
        "applications_skipped": skipped,
        "archived": archived,
    }
