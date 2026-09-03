"""The job's own email address: where job-board applications land.

Job boards will rarely let an ATS pull applications out of them, but they will forward each
applicant to an address you nominate. This exposes that address per job, and a check that reads
the mailbox and turns anything waiting into candidates on the job.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.shared.constants import ModuleScope, PermissionAction
from app.router.enterprise.jobs import _get_scoped_job
from app.router.enterprise.sourcing_chat import _active_connection
from app.services.enterprise import job_inbox as inbox_service

router = APIRouter(prefix="/jobs", tags=["Enterprise Job Inbox"])


def _company_mailbox(current_user: object) -> dict[str, Any] | None:
    company_id = str(getattr(current_user, "company_id", ""))
    return _active_connection(company_id) if company_id else None


@router.get("/{job_id}/inbox")
async def job_inbox_address(
    job_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.read))],
) -> dict[str, Any]:
    """The address to point a job board at, and whether it will actually receive anything."""
    job = await _get_scoped_job(session, current_user, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    conn = _company_mailbox(current_user)
    mailbox = inbox_service.mailbox_address(conn)
    return {
        "address": inbox_service.address_for(job_id, mailbox),
        "mailbox": mailbox,
        # Whose mailbox it is matters: applications land in the company's own inbox when it has
        # connected one, and in the platform's when it has not.
        "mailbox_is_own": bool(conn),
        "connect_url": "/enterprise/sourcing/connections",
    }


@router.post("/{job_id}/inbox/check")
async def check_job_inbox(
    job_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.update))],
) -> dict[str, Any]:
    """Read the mailbox and add anything addressed to this job as a candidate."""
    job = await _get_scoped_job(session, current_user, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return await inbox_service.check_job_inbox(session, job, _company_mailbox(current_user))
