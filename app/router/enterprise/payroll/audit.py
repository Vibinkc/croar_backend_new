import uuid

from fastapi import APIRouter, Depends, Query

from app.models.payroll.audit import AuditLog
from app.payroll.constants import Permission
from app.payroll.deps import DBSessionDep, get_current_company_id, require_permission
from app.schemas.enterprise.payroll.audit import AuditOut
from app.services.payroll import audit_service

router = APIRouter(prefix="/api/v1/enterprise/audit", tags=["audit"])


@router.get("", response_model=list[AuditOut])
async def list_audit(
    db: DBSessionDep,
    limit: int = Query(default=100, ge=1, le=500),
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> list[AuditLog]:
    """Recent activity for the company, newest first."""
    return await audit_service.recent(db, company_id, limit)
