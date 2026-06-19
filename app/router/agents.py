import json
import logging
import os
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from pymongo import MongoClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.shared.agents import AgentAction, ApprovalRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["Agent OS"])

# Require an authenticated user for the agent audit-log / approval endpoints, which
# previously exposed AI action history and pending approvals with no auth at all.
AuthUser = Annotated[object, Depends(get_current_user)]

# Shared Mongo client (single connection pool) for Croar Pilot chat history.
_MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
_MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "croar_sourcing")
_mongo_client = MongoClient(_MONGO_URI)

# TESTING: while True, every candidate invite is redirected to PILOT_TEST_EMAIL instead of the
# real candidate. Flip PILOT_TEST_MODE to False (or set PILOT_TEST_MODE=false env) to send for real.
PILOT_TEST_MODE = os.getenv("PILOT_TEST_MODE", "true").strip().lower() != "false"
PILOT_TEST_EMAIL = os.getenv("PILOT_TEST_EMAIL", "vibi@appxcess.com")


def _pilot_coll():
    return _mongo_client[_MONGO_DB_NAME]["pilot_chat_history"]


class AgentChatRequest(BaseModel):
    message: str
    thread_id: str = "default_thread"
    context: str = "general"
    metadata: dict[str, Any] = {}


class PilotMessage(BaseModel):
    role: str
    content: str


class PilotSession(BaseModel):
    session_id: str | None = None
    title: str
    messages: list[PilotMessage]
    thread_id: str | None = None


class SourceRequest(BaseModel):
    role: str
    skills: str | None = None
    count: int = 10
    location: str | None = None


class InviteCandidate(BaseModel):
    name: str | None = None
    email: str | None = None


class InviteRequest(BaseModel):
    job_id: str
    candidates: list[InviteCandidate]


from langchain_core.messages import HumanMessage

from app.agents.agent import hr_agent_executor


@router.post("/chat")
async def agent_chat(
    request: AgentChatRequest, current_user: AuthUser, session: AsyncSession = Depends(get_db)
):
    """
    Primary endpoint for the Croar Pilot (AI HR agent).
    Executes the LangGraph Agentic workflow with state persistence.
    """
    # The acting company comes from the AUTHENTICATED user — never the chat message —
    # so the Pilot's tools create jobs/automations scoped to the right tenant.
    company_id = str(getattr(current_user, "company_id", "") or "")
    if not company_id:
        raise HTTPException(
            status_code=403,
            detail="Your account isn't linked to a company, so Croar Pilot can't build a pipeline.",
        )

    message = (request.message or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="Message cannot be empty.")

    try:
        inputs = {"messages": [HumanMessage(content=message)]}

        # Namespace the thread by company so conversation state never leaks across tenants.
        thread_id = f"{company_id}:{request.thread_id or 'default_thread'}"
        config = {"configurable": {"thread_id": thread_id, "session": session, "company_id": company_id}}

        # Execute the graph (it resumes from the last state in the thread)
        result = await hr_agent_executor.ainvoke(inputs, config=config)

        # Get the last message from the agent (guard against an empty/odd result).
        messages = result.get("messages") if isinstance(result, dict) else None
        final_message = (
            messages[-1].content if messages else "I couldn't generate a response. Please try again."
        )

        # Surface a UI action from a tool result — the candidate picker (source_candidates) or the
        # pipeline-built result card (build_hiring_pipeline). Take the most recent recognized one.
        pilot_action = None
        ui_tools = {"source_candidates", "build_hiring_pipeline"}
        known_ui = {"candidate_picker", "pipeline_built"}
        try:
            for m in reversed(messages or []):
                if getattr(m, "type", None) == "tool" and getattr(m, "name", "") in ui_tools:
                    data = json.loads(m.content)
                    if isinstance(data, dict) and data.get("ui") in known_ui:
                        pilot_action = data
                        break
        except Exception:
            pilot_action = None

        return {
            "response": final_message,
            "status": "success",
            "pilot_action": pilot_action,
            "metadata": result.get("metadata", {}),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Croar Pilot chat failed")
        raise HTTPException(status_code=500, detail=f"Croar Pilot error: {e}") from e


@router.get("/actions", response_model=list[dict[str, Any]])
async def get_agent_actions(_user: AuthUser, session: AsyncSession = Depends(get_db)):
    """
    Retrieves the audit log of all actions taken by AI agents.
    """
    stmt = select(AgentAction).order_by(AgentAction.created_at.desc()).limit(50)
    result = await session.execute(stmt)
    actions = result.scalars().all()
    return [
        {
            "id": str(a.id),
            "agent_type": a.agent_type,
            "action_type": a.action_type,
            "reasoning": a.reasoning,
            "status": a.status,
            "created_at": a.created_at.isoformat(),
        }
        for a in actions
    ]


@router.get("/approvals", response_model=list[dict[str, Any]])
async def get_pending_approvals(_user: AuthUser, session: AsyncSession = Depends(get_db)):
    """
    Lists all tasks that the AI agents have drafted but require human approval.
    """
    stmt = select(ApprovalRequest).where(ApprovalRequest.status == "pending")
    result = await session.execute(stmt)
    approvals = result.scalars().all()
    return [
        {
            "id": str(ap.id),
            "request_type": ap.request_type,
            "content": ap.content,
            "requested_by": ap.requested_by_agent,
            "created_at": ap.created_at.isoformat(),
        }
        for ap in approvals
    ]


@router.post("/approve/{approval_id}")
async def approve_agent_action(approval_id: str, _user: AuthUser, session: AsyncSession = Depends(get_db)):
    """
    Endpoint for HR/Managers to approve a drafted agent action.
    """
    # Logic to move approval to "approved" and trigger the next step in the graph
    return {"message": f"Approval {approval_id} processed successfully."}


# --- Croar Pilot chat history (per company) ---


@router.post("/pilot/sessions")
async def save_pilot_session(payload: PilotSession, current_user: AuthUser):
    """Create or update a Croar Pilot conversation, scoped to the caller's company."""
    company_id = str(getattr(current_user, "company_id", ""))
    data = payload.model_dump()
    data["company_id"] = company_id
    session_id = data.get("session_id") or str(uuid.uuid4())
    data["session_id"] = session_id
    data["updated_at"] = datetime.now().isoformat()

    try:
        _pilot_coll().update_one(
            {"session_id": session_id, "company_id": company_id},
            {"$set": data, "$setOnInsert": {"created_at": datetime.now().isoformat()}},
            upsert=True,
        )
    except Exception:
        # Chat history is non-critical — never block the conversation if Mongo is down.
        logger.exception("Failed to save pilot session")
        return {"status": "error", "session_id": session_id, "message": "Could not save chat history."}
    return {"status": "success", "session_id": session_id}


@router.get("/pilot/sessions")
async def list_pilot_sessions(current_user: AuthUser):
    """List the company's Croar Pilot conversations (without the message bodies)."""
    company_id = str(getattr(current_user, "company_id", ""))
    try:
        return list(
            _pilot_coll()
            .find({"company_id": company_id}, {"_id": 0, "messages": 0})
            .sort("updated_at", -1)
            .limit(200)
        )
    except Exception:
        logger.exception("Failed to list pilot sessions")
        return []


@router.get("/pilot/sessions/{session_id}")
async def get_pilot_session(session_id: str, current_user: AuthUser):
    """Fetch a single Croar Pilot conversation (verifying company ownership)."""
    company_id = str(getattr(current_user, "company_id", ""))
    try:
        session = _pilot_coll().find_one({"session_id": session_id, "company_id": company_id}, {"_id": 0})
    except Exception:
        logger.exception("Failed to load pilot session")
        return {"error": "Could not load this conversation."}
    if not session:
        return {"error": "Session not found or access denied"}
    return session


@router.delete("/pilot/sessions/{session_id}")
async def delete_pilot_session(session_id: str, current_user: AuthUser):
    """Delete a Croar Pilot conversation (scoped to the caller's company)."""
    company_id = str(getattr(current_user, "company_id", ""))
    try:
        _pilot_coll().delete_one({"session_id": session_id, "company_id": company_id})
    except Exception:
        logger.exception("Failed to delete pilot session")
        return {"status": "error", "message": "Could not delete this conversation."}
    return {"status": "deleted"}


# --- Croar Pilot candidate sourcing + invites ---


@router.post("/pilot/source")
async def pilot_source_candidates(payload: SourceRequest, _user: AuthUser):
    """Live-search candidate profiles for a role across all sourcing platforms. Returns a slim
    list the Pilot UI renders as a selectable checkbox list."""
    from app.router.enterprise.sourcing import backfill_contacts, search_all_platforms

    query = " ".join(p.strip() for p in [payload.role, payload.skills] if p and p.strip())
    if not query:
        raise HTTPException(status_code=422, detail="A role is required to search.")
    count = max(1, min(payload.count or 10, 25))
    try:
        profiles = await search_all_platforms(
            query, payload.location, page=1, page_size=min(max(count, 5), 15)
        )
        # Find emails for the top few so the user sees who's reachable; then email-first sort.
        profiles = await backfill_contacts(profiles, limit=min(count, 8))
        profiles.sort(key=lambda p: 0 if p.get("email") else 1)
        slim = [
            {
                "full_name": p.get("full_name"),
                "headline": p.get("headline"),
                "platform": p.get("platform"),
                "location": p.get("location"),
                "profile_url": p.get("profile_url"),
                "email": p.get("email"),
            }
            for p in profiles[:count]
        ]
        return {"status": "success", "count": len(slim), "profiles": slim}
    except Exception:
        logger.exception("Pilot candidate sourcing failed")
        return {"status": "error", "count": 0, "profiles": [], "message": "Search failed — please try again."}


@router.post("/pilot/invite")
async def pilot_invite_candidates(
    payload: InviteRequest, current_user: AuthUser, session: AsyncSession = Depends(get_db)
):
    """Send the job's application-invite email to the selected candidates. While PILOT_TEST_MODE is
    on, EVERY email is redirected to PILOT_TEST_EMAIL (and clearly marked as a test)."""
    from fastapi.concurrency import run_in_threadpool

    from app.core.settings import get_settings
    from app.models.enterprise.job import JobRequirement
    from app.router.enterprise.communication import send_smtp_email

    settings = get_settings()
    company_id = getattr(current_user, "company_id", None)
    try:
        job_uuid = uuid.UUID(payload.job_id)
    except (ValueError, AttributeError) as e:
        raise HTTPException(status_code=422, detail="Invalid job_id.") from e

    stmt = select(JobRequirement).where(
        JobRequirement.id == job_uuid, JobRequirement.company_id == company_id
    )
    job = (await session.execute(stmt)).scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    # Public candidate-facing job page (has the apply form). NOT the /enterprise API path.
    apply_url = f"{settings.frontend_url}/jobs/{payload.job_id}"
    sent, failed = 0, 0
    for c in payload.candidates:
        real_email = (c.email or "").strip()
        recipient = PILOT_TEST_EMAIL if PILOT_TEST_MODE else real_email
        if not recipient:
            failed += 1
            continue
        name = (c.name or "there").strip()
        test_banner = (
            "<div style='background:#fff3cd;border:1px solid #ffe69c;padding:10px;border-radius:8px;"
            f"margin-bottom:14px;font-size:13px'>🧪 <b>TEST EMAIL</b> — in production this would be sent "
            f"to <b>{name}</b> &lt;{real_email or 'no email found'}&gt;.</div>"
            if PILOT_TEST_MODE
            else ""
        )
        subject = ("[TEST] " if PILOT_TEST_MODE else "") + f"You're invited to apply: {job.title}"
        location_bit = f" in {job.location}" if job.location else ""
        body = (
            f"{test_banner}<p>Hi {name},</p>"
            f"<p>We came across your profile and think you could be a great fit for our "
            f"<strong>{job.title}</strong> role{location_bit}.</p>"
            f'<p><a href="{apply_url}" style="display:inline-block;padding:12px 24px;background:#4f46e5;'
            'color:#fff;text-decoration:none;border-radius:8px;font-weight:bold">Apply now</a></p>'
            "<p>Best regards,<br/>Hiring Team</p>"
        )
        try:
            ok, err = await run_in_threadpool(send_smtp_email, recipient, subject, body, None, None)
            if ok:
                sent += 1
            else:
                failed += 1
                logger.warning("Invite email failed for %s: %s", recipient, err)
        except Exception:
            failed += 1
            logger.exception("Invite email crashed")

    return {
        "status": "success",
        "sent": sent,
        "failed": failed,
        "test_mode": PILOT_TEST_MODE,
        "test_email": PILOT_TEST_EMAIL if PILOT_TEST_MODE else None,
    }
