"""User groups — arrange users into teams, and grant roles to the whole team at once.

Manatal's Groups screen is a list with a name, its members and a description. This keeps that
shape and adds the thing that makes a group worth having: it can carry roles, and every member
holds those roles for as long as they are in it.

Permissions are a union, never a subtraction. A member's effective permissions are their own
roles plus every role of every group they belong to. Leaving a group can only take away what
the group gave; it can never remove something granted directly. The alternative — letting a
group deny — makes "why can this person not see X" unanswerable without simulating the whole
membership graph, and that question gets asked at the worst possible moment.

Deleting a group is a soft delete, so the membership rows survive and a group removed by
mistake can come back with its people still in it. It takes effect immediately either way: the
roles it granted stop applying on the member's next request.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.user_group import UserGroup
from app.models.enterprise.user_role import EnterpriseUser
from app.models.shared.auth import Role
from app.models.shared.constants import ModuleScope, PermissionAction

router = APIRouter(prefix="/user-groups", tags=["User Groups"])

# The chip colours a group can wear. A fixed palette rather than a free hex field: every colour
# here is legible with white text, which a user-chosen one is not guaranteed to be.
GROUP_COLOURS = ("#1976D2", "#00897B", "#7B1FA2", "#C2185B", "#EF6C00", "#5D4037", "#455A64", "#2E7D32")


def _company(user: object) -> Any:
    cid = getattr(user, "company_id", None)
    if not cid:
        raise HTTPException(status_code=404, detail="No company on this account.")
    return cid


def _now() -> datetime:
    """Naive UTC — these columns are TIMESTAMP WITHOUT TIME ZONE."""
    return datetime.now(UTC).replace(tzinfo=None)


def _member_out(u: EnterpriseUser) -> dict[str, Any]:
    name = f"{u.first_name or ''} {u.last_name or ''}".strip()
    return {"id": str(u.id), "name": name or u.email, "email": u.email, "is_active": u.is_active}


def _group_out(g: UserGroup) -> dict[str, Any]:
    return {
        "id": str(g.id),
        "name": g.name,
        "description": g.description,
        "colour": g.colour or GROUP_COLOURS[0],
        "members": [_member_out(u) for u in g.members if u.deleted_at is None],
        "member_count": sum(1 for u in g.members if u.deleted_at is None),
        "roles": [{"id": str(r.id), "name": r.name} for r in g.roles],
        "created_at": g.created_at.isoformat() if g.created_at else None,
    }


async def _load(session: DBSessionDep, cid: Any, group_id: uuid.UUID) -> UserGroup:
    """One group of this company, or 404.

    404 rather than 403 for a group belonging to someone else: a 403 confirms the id exists,
    which is a small leak but a real one across tenants.
    """
    g = (
        await session.execute(
            select(UserGroup).where(
                UserGroup.id == group_id, UserGroup.company_id == cid, UserGroup.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if g is None:
        raise HTTPException(status_code=404, detail="Group not found")
    return g


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    colour: str | None = None


class MembersIn(BaseModel):
    """The complete membership, not a delta.

    Sending the whole set makes the operation idempotent and makes "remove everyone" expressible.
    A delta API needs separate add and remove calls and a client that keeps them in step.
    """

    user_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)


class RolesIn(BaseModel):
    role_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)


@router.get("")
async def list_groups(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.read))
    ],
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
) -> dict[str, Any]:
    """Every group in this company, newest first."""
    cid = _company(current_user)
    where = [UserGroup.company_id == cid, UserGroup.deleted_at.is_(None)]
    if q and q.strip():
        like = f"%{q.strip()}%"
        where.append(or_(UserGroup.name.ilike(like), UserGroup.description.ilike(like)))

    total = (await session.execute(select(func.count()).select_from(UserGroup).where(*where))).scalar_one()
    rows = (
        (
            await session.execute(
                select(UserGroup)
                .where(*where)
                .order_by(UserGroup.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .unique()
        .all()
    )
    return {"total": total, "page": page, "page_size": page_size, "results": [_group_out(g) for g in rows]}


@router.get("/assignable")
async def assignable(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.read))
    ],
) -> dict[str, Any]:
    """The users and roles a group can be built from — everything the create form needs at once."""
    cid = _company(current_user)
    users = (
        (
            await session.execute(
                select(EnterpriseUser)
                .where(EnterpriseUser.company_id == cid, EnterpriseUser.deleted_at.is_(None))
                .order_by(EnterpriseUser.first_name, EnterpriseUser.email)
            )
        )
        .scalars()
        .all()
    )
    # A company's own roles, plus the system roles everyone shares. Roles belonging to another
    # tenant are not offered, which is what keeps a group from granting someone else's access.
    roles = (
        (
            await session.execute(
                select(Role)
                .where(or_(Role.tenant_id == cid, Role.tenant_id.is_(None)))
                .order_by(Role.role_rank, Role.name)
            )
        )
        .scalars()
        .all()
    )
    return {
        "users": [_member_out(u) for u in users],
        "roles": [
            {"id": str(r.id), "name": r.name, "description": r.description, "is_system": r.is_system}
            for r in roles
        ],
        "colours": list(GROUP_COLOURS),
    }


@router.post("", status_code=201)
async def create_group(
    body: GroupIn,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.create))
    ],
) -> dict[str, Any]:
    cid = _company(current_user)
    name = body.name.strip()
    clash = (
        await session.execute(
            select(UserGroup).where(
                UserGroup.company_id == cid,
                func.lower(UserGroup.name) == name.lower(),
                UserGroup.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if clash:
        raise HTTPException(status_code=409, detail=f'There is already a group called "{name}".')

    g = UserGroup(
        name=name,
        description=(body.description or "").strip() or None,
        colour=body.colour if body.colour in GROUP_COLOURS else GROUP_COLOURS[0],
        company_id=cid,
        created_by=getattr(current_user, "id", None),
    )
    session.add(g)
    await session.commit()
    await session.refresh(g)
    return _group_out(g)


@router.patch("/{group_id}")
async def update_group(
    group_id: uuid.UUID,
    body: GroupIn,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.update))
    ],
) -> dict[str, Any]:
    cid = _company(current_user)
    g = await _load(session, cid, group_id)
    name = body.name.strip()
    clash = (
        await session.execute(
            select(UserGroup).where(
                UserGroup.company_id == cid,
                func.lower(UserGroup.name) == name.lower(),
                UserGroup.id != g.id,
                UserGroup.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if clash:
        raise HTTPException(status_code=409, detail=f'There is already a group called "{name}".')

    g.name = name
    g.description = (body.description or "").strip() or None
    if body.colour in GROUP_COLOURS:
        g.colour = body.colour
    await session.commit()
    await session.refresh(g)
    return _group_out(g)


@router.put("/{group_id}/members")
async def set_members(
    group_id: uuid.UUID,
    body: MembersIn,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.update))
    ],
) -> dict[str, Any]:
    """Replace the membership with exactly this set."""
    cid = _company(current_user)
    g = await _load(session, cid, group_id)

    users = (
        (
            await session.execute(
                select(EnterpriseUser).where(
                    EnterpriseUser.id.in_(body.user_ids),
                    # Scoped to the company on the way in. Without this, a valid user id from
                    # another tenant would quietly join the group and inherit its roles.
                    EnterpriseUser.company_id == cid,
                    EnterpriseUser.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
        if body.user_ids
        else []
    )
    missing = len(set(body.user_ids)) - len(users)
    g.members = list(users)
    await session.commit()
    await session.refresh(g)
    out = _group_out(g)
    # Say so rather than silently dropping them: a client that sent a stale id should find out.
    out["ignored"] = missing
    return out


@router.put("/{group_id}/roles")
async def set_roles(
    group_id: uuid.UUID,
    body: RolesIn,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.update))
    ],
) -> dict[str, Any]:
    """Replace the roles this group grants its members."""
    cid = _company(current_user)
    g = await _load(session, cid, group_id)
    roles = (
        (
            await session.execute(
                select(Role).where(
                    Role.id.in_(body.role_ids),
                    # Own roles and shared system roles only. A role from another tenant must
                    # never become grantable through a group.
                    or_(Role.tenant_id == cid, Role.tenant_id.is_(None)),
                )
            )
        )
        .scalars()
        .all()
        if body.role_ids
        else []
    )
    missing = len(set(body.role_ids)) - len(roles)
    g.roles = list(roles)
    await session.commit()
    await session.refresh(g)
    out = _group_out(g)
    out["ignored"] = missing
    return out


@router.delete("/{group_id}", status_code=204)
async def delete_group(
    group_id: uuid.UUID,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.delete))
    ],
) -> None:
    """Soft delete, so a group removed by mistake comes back with its people still in it.

    The roles it granted stop applying immediately — every permission check reads only groups
    with no deleted_at.
    """
    cid = _company(current_user)
    g = await _load(session, cid, group_id)
    g.deleted_at = _now()
    await session.commit()


@router.get("/for-user/{user_id}")
async def groups_for_user(
    user_id: uuid.UUID,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.read))
    ],
) -> dict[str, Any]:
    """Which groups a person is in, and what each one grants them.

    This exists to answer "why can they do that?" without reading the membership table by hand,
    which is the question groups make harder the moment you have more than three.
    """
    cid = _company(current_user)
    u = (
        await session.execute(
            select(EnterpriseUser)
            .options(selectinload(EnterpriseUser.roles))
            .where(
                EnterpriseUser.id == user_id,
                EnterpriseUser.company_id == cid,
                EnterpriseUser.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if u is None:
        raise HTTPException(status_code=404, detail="User not found")

    groups = [g for g in getattr(u, "groups", []) if g.deleted_at is None and g.company_id == cid]
    return {
        "user": _member_out(u),
        "direct_roles": [{"id": str(r.id), "name": r.name} for r in u.roles],
        "groups": [
            {
                "id": str(g.id),
                "name": g.name,
                "colour": g.colour,
                "grants": [{"id": str(r.id), "name": r.name} for r in g.roles],
            }
            for g in groups
        ],
    }
