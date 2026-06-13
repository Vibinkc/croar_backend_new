from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.core.security import get_password_hash
from app.models.enterprise.user_role import EnterpriseUser
from app.models.shared.auth import Permission, Role
from app.models.shared.constants import ModuleScope, PermissionAction
from app.schemas.auth import PermissionSchema, RoleSchema, UserInTeam

router = APIRouter(prefix="/team", tags=["Organization Team Management"])

# Requirements for team management
team_manage_dep = Depends(PermissionChecker(ModuleScope.employees, PermissionAction.moderate))


@router.get("/roles", response_model=list[RoleSchema])
async def list_org_roles(
    session: DBSessionDep, current_user: Annotated[object, team_manage_dep]
) -> list[object]:
    """List all roles available for this organization (including global ones)."""
    # Fetch roles that belong to this tenant or are system roles
    # Note: Tenant ID is coming from the current_user's company_id
    tenant_id = getattr(current_user, "company_id", None)

    stmt = (
        select(Role)
        .options(selectinload(Role.permissions))
        .where(Role.tenant_id == tenant_id, Role.is_system.is_(False))
        .order_by(Role.role_rank.asc())
    )

    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/permissions", response_model=list[PermissionSchema])
async def list_available_permissions(
    session: DBSessionDep, current_user: Annotated[object, team_manage_dep]
) -> list[object]:
    """List all permissions that can be assigned to roles."""
    tenant_id = getattr(current_user, "company_id", None)
    stmt = select(Permission).where(
        ((Permission.tenant_id == tenant_id) | (Permission.tenant_id.is_(None))),
        Permission.module != ModuleScope.platform,
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.post("/roles", response_model=RoleSchema)
async def create_org_role(
    session: DBSessionDep,
    current_user: Annotated[object, team_manage_dep],
    name: str = Body(...),
    description: str = Body(None),
    permission_ids: list[UUID] = Body([]),
) -> object:
    """Create a custom role for the organization."""
    tenant_id = getattr(current_user, "company_id", None)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="User not associated with an organization.")

    # Check if role exists
    stmt = select(Role).where(Role.name == name, Role.tenant_id == tenant_id)
    result = await session.execute(stmt)
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Role already exists.")

    new_role = Role(
        name=name,
        description=description,
        tenant_id=tenant_id,
        is_system=False,
        role_rank=50,  # Custom roles have lower priority
    )

    if permission_ids:
        # Fetch permissions but EXCLUDE platform module for tenant roles
        perm_stmt = select(Permission).where(
            Permission.id.in_(permission_ids), Permission.module != ModuleScope.platform
        )
        perms = (await session.execute(perm_stmt)).scalars().all()
        new_role.permissions = perms

    session.add(new_role)
    await session.commit()
    await session.refresh(new_role, ["permissions"])
    return new_role


@router.put("/roles/{role_id}", response_model=RoleSchema)
async def update_org_role(
    role_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[object, team_manage_dep],
    name: str = Body(None),
    description: str = Body(None),
    permission_ids: list[UUID] = Body(None),
) -> object:
    """Update a custom role for the organization."""
    tenant_id = getattr(current_user, "company_id", None)

    stmt = (
        select(Role)
        .options(selectinload(Role.permissions))
        .where(
            Role.id == role_id,
            Role.tenant_id == tenant_id,
            Role.is_system.is_(False),  # Cannot edit system roles
        )
    )
    result = await session.execute(stmt)
    role = result.scalar_one_or_none()

    if not role:
        raise HTTPException(status_code=404, detail="Role not found or is a system role.")

    if name is not None:
        role.name = name
    if description is not None:
        role.description = description

    if permission_ids is not None:
        # Fetch permissions but EXCLUDE platform module for tenant roles
        perm_stmt = select(Permission).where(
            Permission.id.in_(permission_ids), Permission.module != ModuleScope.platform
        )
        perms = (await session.execute(perm_stmt)).scalars().all()
        role.permissions = perms

    await session.commit()
    await session.refresh(role, ["permissions"])
    return role


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_org_role(
    role_id: UUID, session: DBSessionDep, current_user: Annotated[object, team_manage_dep]
) -> None:
    """Delete a custom role for the organization."""
    tenant_id = getattr(current_user, "company_id", None)

    stmt = select(Role).where(
        Role.id == role_id,
        Role.tenant_id == tenant_id,
        Role.is_system.is_(False),  # Cannot delete system roles
    )
    result = await session.execute(stmt)
    role = result.scalar_one_or_none()

    if not role:
        raise HTTPException(status_code=404, detail="Role not found or is a system role.")

    await session.delete(role)
    await session.commit()
    return


@router.get("/members", response_model=list[UserInTeam])
async def list_team_members(
    session: DBSessionDep, current_user: Annotated[object, team_manage_dep]
) -> list[object]:
    """List all members of the organization with their roles."""
    tenant_id = getattr(current_user, "company_id", None)
    if not tenant_id:
        return []

    stmt = (
        select(EnterpriseUser)
        .options(selectinload(EnterpriseUser.roles).selectinload(Role.permissions))
        .where(EnterpriseUser.company_id == tenant_id, EnterpriseUser.deleted_at.is_(None))
    )

    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.post("/members", response_model=UserInTeam)
async def add_team_member(
    session: DBSessionDep,
    current_user: Annotated[object, team_manage_dep],
    email: str = Body(...),
    password: str = Body(...),
    first_name: str = Body(...),
    last_name: str = Body(...),
    role_ids: list[UUID] = Body(...),
) -> object:
    """Add a new member to the organization team."""
    tenant_id = getattr(current_user, "company_id", None)

    # Verify the roles belong to this tenant or are platform roles
    role_stmt = (
        select(Role)
        .options(selectinload(Role.permissions))
        .where(Role.id.in_(role_ids), (Role.tenant_id == tenant_id) | (Role.tenant_id.is_(None)))
    )
    result = await session.execute(role_stmt)
    roles = result.scalars().all()

    if not roles:
        raise HTTPException(status_code=400, detail="Invalid roles provided.")

    new_user = EnterpriseUser(
        email=email,
        password_hash=get_password_hash(password),
        first_name=first_name,
        last_name=last_name,
        company_id=tenant_id,
        is_active=True,
    )
    new_user.roles = roles

    session.add(new_user)
    await session.commit()

    # Reload with relationships for response
    stmt = (
        select(EnterpriseUser)
        .options(selectinload(EnterpriseUser.roles).selectinload(Role.permissions))
        .where(EnterpriseUser.id == new_user.id)
    )
    result = await session.execute(stmt)
    return result.scalar_one()
