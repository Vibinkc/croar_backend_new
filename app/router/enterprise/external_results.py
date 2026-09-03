"""Endpoints for results coming back from a third-party assessment tool.

Three of them, because there are three honest ways a score gets here:

* the provider posts it (Testlify and most tools of that class support webhooks),
* a recruiter types it in, which works today and needs no public URL, and
* the recruiter asks for the webhook URL to paste into the provider.

The webhook is unauthenticated in the usual sense — a third party cannot hold a bearer token —
so the company is identified by an HMAC in the path. That is the same shape as the per-job
email address: unguessable, revocable by rotating the secret, and it never exposes an id.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.candidate import CandidateApplication
from app.models.enterprise.company import Company
from app.models.shared.constants import ModuleScope, PermissionAction
from app.services.enterprise import external_results as svc

router = APIRouter(tags=["Assessment results"])


class WebhookResult(BaseModel):
    """What a provider posts when a candidate finishes.

    Field names differ per provider, so the common spellings are accepted rather than demanding
    one shape — a webhook that 422s because the provider says "candidate_email" instead of
    "email" is a webhook nobody can use.
    """

    email: str | None = None
    candidate_email: str | None = None
    score: float | None = None
    total_score: float | None = None
    percentage: float | None = None
    status: str = "COMPLETED"
    job_id: UUID | None = None

    def resolved_email(self) -> str:
        return (self.email or self.candidate_email or "").strip().lower()

    def resolved_score(self) -> float | None:
        for value in (self.score, self.total_score, self.percentage):
            if value is not None:
                return value
        return None


@router.post("/public/assessments/result/{token}")
async def receive_result(
    token: str,
    body: WebhookResult,
    request: Request,
    session: DBSessionDep,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """A provider reporting that a candidate finished a test.

    Returns 200 with an explanation for a result that cannot be matched, rather than an error
    status: providers retry on failure, and retrying will not make an unknown email known.
    """
    companies = (await session.execute(select(Company))).scalars().all()
    company = next((c for c in companies if svc.company_token(c.id) == token), None)
    if not company:
        raise HTTPException(status_code=404, detail="Unknown webhook token.")

    email = body.resolved_email()
    if not email:
        return {"status": "ignored", "reason": "No candidate email in the payload."}

    application = await svc.find_application(session, company.id, email, body.job_id)
    if not application:
        return {"status": "ignored", "reason": f"No candidate on this account with the email {email}."}

    raw = await request.json()
    result = await svc.record_result(
        session,
        application,
        body.resolved_score(),
        provider=str(raw.get("provider") or "External"),
        status=body.status or "COMPLETED",
        raw=raw if isinstance(raw, dict) else {},
        background_tasks=background_tasks,
    )
    return {"status": "recorded", **result}


class ManualResult(BaseModel):
    score: float = Field(ge=0, le=100)
    provider: str = "External"
    status: str = "COMPLETED"
    note: str | None = None


@router.post("/enterprise/applications/{application_id}/external-result")
async def record_manual_result(
    application_id: UUID,
    body: ManualResult,
    session: DBSessionDep,
    background_tasks: BackgroundTasks,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.assessments, PermissionAction.moderate))
    ],
) -> dict[str, Any]:
    """A recruiter recording what a candidate scored in an external tool.

    The path that works without a public URL or a provider that can post anywhere — which is
    most of them, most of the time.
    """
    company_id = getattr(current_user, "company_id", None)
    application = (
        await session.execute(
            select(CandidateApplication).where(
                CandidateApplication.id == application_id,
                CandidateApplication.company_id == company_id,
                CandidateApplication.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    return await svc.record_result(
        session,
        application,
        body.score,
        provider=body.provider,
        status=body.status,
        raw={"recorded_by": str(getattr(current_user, "email", "")), "note": body.note},
        background_tasks=background_tasks,
    )


@router.get("/enterprise/integrations/webhook")
async def webhook_details(
    request: Request,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.read))],
) -> dict[str, Any]:
    """The URL to paste into a provider's webhook settings."""
    company_id = getattr(current_user, "company_id", None)
    if not company_id:
        raise HTTPException(status_code=404, detail="No company on this account.")
    base = str(request.base_url).rstrip("/")
    token = svc.company_token(company_id)
    return {
        "url": f"{base}/api/v1/public/assessments/result/{token}",
        # Said plainly because it is the difference between a webhook that works and one that
        # silently never arrives.
        "reachable": not any(h in base for h in ("localhost", "127.0.0.1", "0.0.0.0")),  # nosec B104
        "expects": {
            "email": "the candidate's email address, as the provider collected it",
            "score": "a number out of 100 (total_score and percentage are also accepted)",
            "status": "COMPLETED",
        },
    }
