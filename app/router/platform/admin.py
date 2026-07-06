import secrets
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.core.security import get_password_hash
from app.core.settings import get_settings
from app.models.enterprise.company import Company
from app.models.enterprise.user_role import EnterpriseUser
from app.models.shared.auth import Permission, Role
from app.models.shared.constants import ModuleScope, PermissionAction
from app.schemas.enterprise.company import CompanyResponse, CompanyUpdate
from app.schemas.rbac import PermissionOut, RoleCreate, RoleOut, RoleUpdate

router = APIRouter(tags=["Platform Administration"])

# Super Admin only dependency
platform_admin_dep = Depends(PermissionChecker(ModuleScope.platform, PermissionAction.moderate))


def _email_configured() -> bool:
    """Whether outbound email can plausibly be sent (used by provisioning /
    password-reset). Missing SMTP host or sender means those emails silently fail."""
    s = get_settings()
    return bool(getattr(s, "smtp_address", None) and getattr(s, "mailer_sender_email", None))


@router.get("/stats")
async def get_platform_stats(
    session: DBSessionDep, _admin: Annotated[object, platform_admin_dep]
) -> dict[str, object]:
    """Get high-level platform statistics + a real system-health signal for the
    Super Admin dashboard.

    ``system_status`` is derived from live checks rather than a constant:
    - **Down** — the database ping fails (core dependency unreachable).
    - **Degraded** — DB is up but outbound email isn't configured (so provisioning
      / password-reset emails won't send).
    - **Operational** — all checks pass.
    """
    health: dict[str, str] = {
        "database": "up",
        "email": "configured" if _email_configured() else "unconfigured",
    }

    # DB liveness — probed defensively so a blip degrades the signal instead of 500-ing.
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        health["database"] = "down"

    total_orgs = total_users = total_roles = 0
    if health["database"] == "up":
        try:
            total_orgs = (
                await session.execute(select(func.count(Company.id)).where(Company.deleted_at.is_(None)))
            ).scalar() or 0
            total_users = (
                await session.execute(
                    select(func.count(EnterpriseUser.id)).where(EnterpriseUser.deleted_at.is_(None))
                )
            ).scalar() or 0
            total_roles = (
                await session.execute(select(func.count(Role.id)).where(Role.tenant_id.is_(None)))
            ).scalar() or 0
        except Exception:
            health["database"] = "down"

    if health["database"] == "down":
        system_status = "Down"
    elif health["email"] != "configured":
        system_status = "Degraded"
    else:
        system_status = "Operational"

    return {
        "tenants": total_orgs,
        "users": total_users,
        "global_roles": total_roles,
        "system_status": system_status,
        "health": health,
    }


@router.get("/tenants", response_model=list[CompanyResponse])
async def list_tenants(session: DBSessionDep, _admin: Annotated[object, platform_admin_dep]) -> list[object]:
    """List all tenants (organizations) in the platform."""
    stmt = select(Company).where(Company.deleted_at.is_(None))
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.post("/tenants", response_model=CompanyResponse)
async def create_tenant(
    session: DBSessionDep, _admin: Annotated[object, platform_admin_dep], body: dict[str, Any] = Body(...)
) -> object:
    """Create a new tenant and its first admin user."""
    import re

    # Resolve org_data and admin fields
    if "org_data" in body:
        org_dict = body["org_data"]
        admin_email = body.get("admin_email")
        admin_password = body.get("admin_password")
        admin_profile_image = body.get("admin_profile_image")
    else:
        org_dict = body
        admin_email = body.get("admin_email")
        admin_password = body.get("admin_password")
        admin_profile_image = body.get("admin_profile_image")

    if not admin_email or not admin_password:
        raise HTTPException(status_code=400, detail="admin_email and admin_password are required")

    org_name = org_dict.get("name")
    if not org_name:
        raise HTTPException(status_code=400, detail="Organization name is required")

    # Reject a duplicate admin email up front (globally unique) with a clear message.
    if (
        await session.execute(select(EnterpriseUser.id).where(EnterpriseUser.email == admin_email))
    ).scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"A user with the email '{admin_email}' already exists.")

    # 1. Create Company
    slug = org_dict.get("slug")
    if not slug:
        slug = re.sub(r"[^a-zA-Z0-9]", "-", org_name.lower())
        slug = re.sub(r"-+", "-", slug).strip("-")

    # Slug is globally unique — auto-dedupe (acme, acme-2, acme-3, …) so a name
    # collision with an existing (or archived) org doesn't 500.
    base_slug = slug or "org"
    slug = base_slug
    suffix = 1
    while (
        await session.execute(select(Company.id).where(Company.slug == slug))
    ).scalar_one_or_none() is not None:
        suffix += 1
        slug = f"{base_slug}-{suffix}"

    company_fields = {"name", "logo_url", "industry", "location", "config", "is_consultancy", "parent_id"}
    filtered_org = {k: v for k, v in org_dict.items() if k in company_fields}

    # A client org may be linked to a parent consultancy — validate it.
    parent_id = filtered_org.get("parent_id")
    if parent_id:
        parent = await session.get(Company, parent_id)
        if parent is None or parent.deleted_at is not None:
            raise HTTPException(status_code=400, detail="Parent consultancy not found.")
        if not parent.is_consultancy:
            raise HTTPException(
                status_code=400, detail="The selected parent organization is not a consultancy."
            )

    new_company = Company(slug=slug, **filtered_org)
    session.add(new_company)
    await session.flush()  # Get company ID

    # 2. Get/Create ADMIN role for this company
    stmt = select(Role).where(Role.name == "ADMIN", Role.tenant_id == new_company.id)
    result = await session.execute(stmt)
    admin_role = result.scalar_one_or_none()

    if not admin_role:
        admin_role = Role(
            name="ADMIN",
            description=f"Administrator for {new_company.name}",
            tenant_id=new_company.id,
            is_system=True,
            role_rank=1,
        )
        # Assign all non-platform permissions to this role BEFORE adding/flushing to avoid lazy loading trigger
        perm_stmt = select(Permission).where(Permission.module != ModuleScope.platform)
        perms = (await session.execute(perm_stmt)).scalars().all()
        admin_role.permissions = perms

        session.add(admin_role)
        await session.flush()

    # 3. Create Admin User for the company
    new_user = EnterpriseUser(
        email=admin_email,
        password_hash=get_password_hash(admin_password),
        first_name="Admin",
        last_name=new_company.name,
        profile_image=admin_profile_image,
        company_id=new_company.id,
        is_active=True,
    )
    # Assign roles BEFORE adding/flushing to prevent database lazy load queries
    new_user.roles = [admin_role]

    session.add(new_user)
    await session.flush()

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="An organization with this name/slug or admin email already exists."
        ) from None
    await session.refresh(new_company)
    return new_company


@router.get("/tenants/{tenant_id}", response_model=CompanyResponse)
async def get_tenant_details(
    tenant_id: UUID, session: DBSessionDep, _admin: Annotated[object, platform_admin_dep]
) -> object:
    tenant = await session.get(Company, tenant_id)
    if not tenant or tenant.deleted_at:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


@router.put("/tenants/{tenant_id}", response_model=CompanyResponse)
async def update_tenant(
    tenant_id: UUID,
    tenant_in: CompanyUpdate,
    session: DBSessionDep,
    _admin: Annotated[object, platform_admin_dep],
) -> object:
    tenant = await session.get(Company, tenant_id)
    if not tenant or tenant.deleted_at:
        raise HTTPException(status_code=404, detail="Tenant not found")

    update_data = tenant_in.model_dump(exclude_unset=True)

    # Validate a (re)assigned parent consultancy.
    parent_id = update_data.get("parent_id")
    if parent_id:
        if str(parent_id) == str(tenant_id):
            raise HTTPException(status_code=400, detail="An organization can't be its own parent.")
        parent = await session.get(Company, parent_id)
        if parent is None or parent.deleted_at is not None:
            raise HTTPException(status_code=400, detail="Parent consultancy not found.")
        if not parent.is_consultancy:
            raise HTTPException(
                status_code=400, detail="The selected parent organization is not a consultancy."
            )

    for key, value in update_data.items():
        setattr(tenant, key, value)

    await session.commit()
    await session.refresh(tenant)
    return tenant


@router.delete("/tenants/{tenant_id}")
async def delete_tenant(
    tenant_id: UUID, session: DBSessionDep, _admin: Annotated[object, platform_admin_dep]
) -> dict[str, str]:
    tenant = await session.get(Company, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    tenant.deleted_at = cast("Any", func.now())
    await session.commit()
    return {"status": "success"}


# --- Sub-resources ---


def _user_out(u: EnterpriseUser, *, role: str | None = None) -> dict[str, object]:
    """Serialise a tenant user for the admin UI. A raw ORM object can't be
    returned through a Pydantic response_model, so build a plain dict. `role`
    can be passed for freshly-created users whose `roles` aren't eager-loaded."""
    if role is None:
        loaded = getattr(u, "roles", None) or []
        role = loaded[0].name if loaded else ""
    return {
        "id": str(u.id),
        "first_name": u.first_name,
        "last_name": u.last_name,
        "email": u.email,
        "role": role,
        "is_active": u.is_active,
    }


@router.get("/tenants/{tenant_id}/admins")
async def list_tenant_admins(
    tenant_id: UUID, session: DBSessionDep, _admin: Annotated[object, platform_admin_dep]
) -> list[dict[str, object]]:
    # Fetch users with ADMIN role for this tenant
    stmt = (
        select(EnterpriseUser)
        .join(EnterpriseUser.roles)
        .where(EnterpriseUser.company_id == tenant_id, Role.name == "ADMIN")
        .options(selectinload(EnterpriseUser.roles))
    )
    result = await session.execute(stmt)
    return [_user_out(u) for u in result.scalars().all()]


@router.post("/tenants/{tenant_id}/admins")
async def create_tenant_admin(
    tenant_id: UUID,
    session: DBSessionDep,
    _admin: Annotated[object, platform_admin_dep],
    admin_data: dict[str, object] = Body(...),
) -> dict[str, object]:
    # Get/Create ADMIN role for this company
    stmt = select(Role).where(Role.name == "ADMIN", Role.tenant_id == tenant_id)
    result = await session.execute(stmt)
    admin_role = result.scalar_one_or_none()

    if not admin_role:
        admin_role = Role(
            name="ADMIN",
            description="Company Administrator",
            tenant_id=tenant_id,
            is_system=True,
            role_rank=1,
        )
        session.add(admin_role)
        await session.flush()

        # Assign non-platform permissions
        perm_stmt = select(Permission).where(Permission.module != ModuleScope.platform)
        perms = (await session.execute(perm_stmt)).scalars().all()
        admin_role.permissions = perms

    admin_email = admin_data.get("email")
    if not admin_email:
        raise HTTPException(status_code=400, detail="email is required")
    if (
        await session.execute(select(EnterpriseUser).where(EnterpriseUser.email == admin_email))
    ).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="A user with this email already exists")

    password = admin_data.get("password") or secrets.token_urlsafe(16)
    new_user = EnterpriseUser(
        email=cast("str", admin_email),
        password_hash=get_password_hash(cast("str", password)),
        first_name=cast("str", admin_data.get("first_name", "Admin")),
        last_name=cast("str", admin_data.get("last_name", "User")),
        company_id=tenant_id,
        is_active=True,
    )
    session.add(new_user)
    await session.flush()
    new_user.roles.append(admin_role)

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="A user with this email already exists") from None
    await session.refresh(new_user)
    return _user_out(new_user, role=admin_role.name)


@router.get("/tenants/{tenant_id}/users")
async def list_tenant_users(
    tenant_id: UUID, session: DBSessionDep, _admin: Annotated[object, platform_admin_dep]
) -> list[dict[str, object]]:
    stmt = (
        select(EnterpriseUser)
        .where(EnterpriseUser.company_id == tenant_id)
        .options(selectinload(EnterpriseUser.roles))
    )
    result = await session.execute(stmt)
    return [_user_out(u) for u in result.scalars().all()]


@router.post("/tenants/{tenant_id}/users")
async def create_tenant_user(
    tenant_id: UUID,
    session: DBSessionDep,
    _admin: Annotated[object, platform_admin_dep],
    user_data: dict[str, object] = Body(...),
) -> dict[str, object]:
    # This is a guestimated implementation based on common patterns
    # In a real scenario, we'd use a specific schema
    user_email = user_data.get("email")
    if not user_email:
        raise HTTPException(status_code=400, detail="email is required")
    first_name = user_data.get("first_name")
    if not first_name:
        raise HTTPException(status_code=400, detail="first_name is required")
    if (
        await session.execute(select(EnterpriseUser).where(EnterpriseUser.email == user_email))
    ).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="A user with this email already exists")

    password = user_data.get("password") or secrets.token_urlsafe(16)
    new_user = EnterpriseUser(
        email=cast("str", user_email),
        password_hash=get_password_hash(cast("str", password)),
        first_name=cast("str", first_name),
        last_name=cast("str", user_data.get("last_name") or ""),
        company_id=tenant_id,
        is_active=True,
    )
    session.add(new_user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="A user with this email already exists") from None
    await session.refresh(new_user)
    return _user_out(new_user, role=str(user_data.get("role") or ""))


@router.delete("/tenants/{tenant_id}/users/{user_id}")
async def delete_tenant_user(
    tenant_id: UUID, user_id: UUID, session: DBSessionDep, _admin: Annotated[object, platform_admin_dep]
) -> dict[str, str]:
    stmt = select(EnterpriseUser).where(EnterpriseUser.id == user_id, EnterpriseUser.company_id == tenant_id)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found in this tenant")

    await session.delete(user)
    await session.commit()
    return {"status": "success"}


@router.get("/roles", response_model=list[RoleOut])
async def list_global_roles(
    session: DBSessionDep, _admin: Annotated[object, platform_admin_dep]
) -> list[object]:
    """List all global (system-wide) roles."""
    stmt = (
        select(Role)
        .options(selectinload(Role.permissions))
        .where(Role.tenant_id.is_(None))
        .order_by(Role.role_rank.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.post("/roles", response_model=RoleOut)
async def create_global_role(
    role_in: RoleCreate, session: DBSessionDep, _admin: Annotated[object, platform_admin_dep]
) -> object:
    """Create a new global system role."""
    # Check if role exists
    stmt = select(Role).where(Role.name == role_in.name, Role.tenant_id.is_(None))
    if (await session.execute(stmt)).scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Role already exists")

    new_role = Role(
        name=role_in.name, description=role_in.description, role_rank=role_in.role_rank, is_system=False
    )
    session.add(new_role)
    await session.flush()

    if role_in.permission_ids:
        perm_stmt = select(Permission).where(Permission.id.in_(role_in.permission_ids))
        perms = (await session.execute(perm_stmt)).scalars().all()
        new_role.permissions = list(perms)

    await session.commit()
    await session.refresh(new_role)
    return new_role


@router.get("/roles/{role_id}", response_model=RoleOut)
async def get_role_details(
    role_id: UUID, session: DBSessionDep, _admin: Annotated[object, platform_admin_dep]
) -> object:
    role = await session.get(Role, role_id)
    if not role or role.tenant_id:
        raise HTTPException(status_code=404, detail="Global role not found")
    return role


@router.put("/roles/{role_id}", response_model=RoleOut)
async def update_global_role(
    role_id: UUID, role_in: RoleUpdate, session: DBSessionDep, _admin: Annotated[object, platform_admin_dep]
) -> object:
    """Update a global role's metadata and permissions."""
    stmt = (
        select(Role)
        .options(selectinload(Role.permissions))
        .where(Role.id == role_id, Role.tenant_id.is_(None))
    )
    result = await session.execute(stmt)
    role = result.scalar_one_or_none()

    if not role:
        raise HTTPException(status_code=404, detail="Global role not found")

    if role.is_system:
        # System roles (e.g. SUPER_ADMIN) are seeded and load-bearing — renaming them or
        # stripping their permissions could lock the platform out. They are read-only.
        raise HTTPException(status_code=400, detail="System roles are read-only and cannot be edited.")

    update_data = role_in.model_dump(exclude={"permission_ids"}, exclude_unset=True)
    for key, value in update_data.items():
        setattr(role, key, value)

    if role_in.permission_ids is not None:
        perm_stmt = select(Permission).where(Permission.id.in_(role_in.permission_ids))
        perms = (await session.execute(perm_stmt)).scalars().all()
        role.permissions = list(perms)

    await session.commit()
    await session.refresh(role)
    return role


@router.delete("/roles/{role_id}")
async def delete_global_role(
    role_id: UUID, session: DBSessionDep, _admin: Annotated[object, platform_admin_dep]
) -> dict[str, str]:
    """Delete a custom global role. System roles protected."""
    role = await session.get(Role, role_id)
    if not role or role.tenant_id:
        raise HTTPException(status_code=404, detail="Global role not found")

    if role.is_system:
        raise HTTPException(status_code=400, detail="Cannot delete system roles")

    await session.delete(role)
    try:
        await session.commit()
    except IntegrityError:
        # Role is still referenced (e.g. a user's legacy role_id FK) — deleting would orphan it.
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="This role is still assigned to one or more users and can't be deleted."
        ) from None
    return {"status": "success"}


@router.get("/permissions", response_model=list[PermissionOut])
async def list_all_permissions(
    session: DBSessionDep, _admin: Annotated[object, platform_admin_dep]
) -> list[object]:
    """List all available permissions in the system."""
    stmt = (
        select(Permission)
        .where(Permission.tenant_id.is_(None))
        .order_by(Permission.module.asc(), Permission.resource.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
