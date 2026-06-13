from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, select

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.user_role import EnterpriseUser
from app.models.shared.audit_log import AuditLog
from app.models.shared.constants import ModuleScope, PermissionAction
from app.models.shared.system_settings import SystemSettings

router = APIRouter(prefix="/system", tags=["System Settings"])
platform_admin_dep = Depends(PermissionChecker(ModuleScope.platform, PermissionAction.moderate))

# Only these non-sensitive flags may be read without authentication (the login/signup
# pages need them pre-auth). Every other key is admin-only via GET /system/settings.
_PUBLIC_SETTING_KEYS = {"signup_enabled", "google_sso_enabled", "microsoft_sso_enabled"}


class SettingUpdate(BaseModel):
    value: bool | str


@router.get("/settings")
async def get_all_settings(session: DBSessionDep, _admin: Annotated[object, platform_admin_dep]):
    """Get all global system settings."""
    stmt = select(SystemSettings)
    result = await session.execute(stmt)
    return result.scalars().all()


@router.get("/settings/{key}")
async def get_setting(key: str, session: DBSessionDep):
    """Publicly readable check, restricted to a small allowlist of non-sensitive flags."""
    if key not in _PUBLIC_SETTING_KEYS:
        raise HTTPException(status_code=404, detail="Setting not found")
    stmt = select(SystemSettings).where(SystemSettings.key == key)
    setting = (await session.execute(stmt)).scalar_one_or_none()
    if not setting:
        return {"key": key, "value": None}
    return {
        "key": setting.key,
        "value": setting.value_bool if setting.value_bool is not None else setting.value_str,
    }


@router.patch("/settings/{key}")
async def update_setting(
    key: str, data: SettingUpdate, session: DBSessionDep, _admin: Annotated[object, platform_admin_dep]
):
    """Update a specific system setting."""
    stmt = select(SystemSettings).where(SystemSettings.key == key)
    setting = (await session.execute(stmt)).scalar_one_or_none()
    if not setting:
        raise HTTPException(status_code=404, detail="Setting not found")

    if isinstance(data.value, bool):
        setting.value_bool = data.value
    else:
        setting.value_str = data.value

    # Audit Log
    log = AuditLog(
        admin_id=_admin.id if hasattr(_admin, "id") else None,
        action="UPDATE_SYSTEM_SETTING",
        entity_id=setting.id,
        details={"key": key, "new_value": data.value},
    )
    session.add(log)

    await session.commit()
    return setting


@router.get("/audit-logs")
async def get_audit_logs(
    session: DBSessionDep, _admin: Annotated[object, platform_admin_dep], limit: int = 100
):
    """Get recent audit logs for the whole platform."""
    stmt = select(AuditLog).order_by(desc(AuditLog.timestamp)).limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()


@router.get("/users")
async def get_all_users(session: DBSessionDep, _admin: Annotated[object, platform_admin_dep]):
    """List all users who registered via the public signup flow."""
    stmt = (
        select(EnterpriseUser)
        .where(EnterpriseUser.is_self_registered.is_(True))
        .order_by(EnterpriseUser.created_at.desc())
    )
    result = await session.execute(stmt)
    return result.scalars().all()


@router.patch("/users/{user_id}/toggle-status")
async def toggle_user_status(
    user_id: str, session: DBSessionDep, _admin: Annotated[object, platform_admin_dep]
):
    """Enable or disable a user account."""
    stmt = select(EnterpriseUser).where(EnterpriseUser.id == user_id)
    user = (await session.execute(stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = not user.is_active
    await session.commit()
    return {"status": "success", "is_active": user.is_active}


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, session: DBSessionDep, _admin: Annotated[object, platform_admin_dep]):
    """Permanently delete a user account."""
    stmt = select(EnterpriseUser).where(EnterpriseUser.id == user_id)
    user = (await session.execute(stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    await session.delete(user)
    await session.commit()
    return {"status": "success"}
