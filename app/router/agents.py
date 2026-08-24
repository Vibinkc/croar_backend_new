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
    # No default — the caller must state how many to source (consistent with the Pilot's
    # ask-how-many flow); the tool/endpoint never silently assumes a number.
    count: int
    location: str | None = None


class InviteCandidate(BaseModel):
    name: str | None = None
    email: str | None = None
    # Optional profile context (sent by the Pilot candidate picker) so the job's Sourcing tab can
    # show where each invited person came from.
    platform: str | None = None
    profile_url: str | None = None
    headline: str | None = None
    location: str | None = None


class InviteRequest(BaseModel):
    job_id: str
    candidates: list[InviteCandidate]


from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.agent import get_agent_executor


def _message_text(content: object) -> str:
    """Flatten a LangChain message's content to plain text.

    Claude (extended thinking) returns content as a LIST of blocks — thinking, text,
    tool_use — instead of a plain string. The chat UI renders the response as markdown, so
    we keep only the text blocks and drop thinking/tool blocks.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                parts.append(str(block["text"]))
        return "".join(parts).strip() or "Done."
    return str(content)


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

    # Namespace the thread by company so conversation state never leaks across tenants.
    thread_id = f"{company_id}:{request.thread_id or 'default_thread'}"
    try:
        # If the UI handed off a specific job (e.g. "Source with Croar Pilot" after creating a job),
        # give the agent the EXACT job_id so it never has to guess between similarly-named jobs or
        # ask the user which one again. This context stays out of the visible chat.
        turn_messages: list = []
        meta = request.metadata or {}
        source_job_id = str(meta.get("source_job_id") or "").strip()
        source_job_title = str(meta.get("source_job_title") or "").strip()
        if source_job_id:
            turn_messages.append(
                SystemMessage(
                    content=(
                        f"CONTEXT: The user is sourcing candidates for an EXISTING job in their company — "
                        f"job_id='{source_job_id}'"
                        + (f", title='{source_job_title}'" if source_job_title else "")
                        + ". When they give a candidate count, call source_candidates with EXACTLY this "
                        "job_id. Do NOT ask which job it is, do NOT call list_jobs to disambiguate, and "
                        "do NOT build a new pipeline for it."
                    )
                )
            )
        turn_messages.append(HumanMessage(content=message))
        inputs = {"messages": turn_messages}
        config = {"configurable": {"thread_id": thread_id, "session": session, "company_id": company_id}}

        # Execute the graph (it resumes from the last state in the thread). The executor uses a
        # persistent Postgres checkpointer when available, else an in-memory fallback.
        executor = await get_agent_executor()
        try:
            result = await executor.ainvoke(inputs, config=config)
        except Exception as turn_err:
            # A corrupted/oversized conversation state (e.g. an invalid message in the replayed
            # history) makes EVERY retry on the same thread fail the same way. Recover by replaying
            # just this turn on a fresh thread so the user isn't trapped in a failure loop.
            logger.warning(
                f"Croar Pilot turn failed on thread '{thread_id}' ({turn_err!r}); retrying on a fresh thread."
            )
            fresh_thread = f"{company_id}:recovered-{uuid.uuid4().hex[:8]}"
            fresh_config = {
                "configurable": {"thread_id": fresh_thread, "session": session, "company_id": company_id}
            }
            result = await executor.ainvoke(inputs, config=fresh_config)

        # Get the last message from the agent (guard against an empty/odd result).
        messages = result.get("messages") if isinstance(result, dict) else None
        final_message = (
            _message_text(messages[-1].content)
            if messages
            else "I couldn't generate a response. Please try again."
        )

        # Surface a UI action from a tool result — the candidate picker (source_candidates) or the
        # pipeline-built result card (build_hiring_pipeline). ONLY look at THIS turn's tool calls
        # (iterate back until the current user message): otherwise a later turn that produced no UI
        # action (e.g. sourcing returned "need_count") would re-surface an OLD action and the card
        # would render a second time.
        pilot_action = None
        ui_tools = {"source_candidates", "build_hiring_pipeline"}
        known_ui = {"candidate_picker", "pipeline_built"}
        try:
            for m in reversed(messages or []):
                if getattr(m, "type", None) == "human":
                    break  # reached the current user message — stop; don't reuse prior turns' actions
                if getattr(m, "type", None) == "tool" and getattr(m, "name", "") in ui_tools:
                    data = json.loads(m.content)
                    if isinstance(data, dict) and data.get("ui") in known_ui:
                        pilot_action = data
                        break
        except Exception:
            pilot_action = None

        # Meter the Pilot turn on real token burn (langchain, not claude_complete, so not
        # auto-metered). Sum token usage across the agent's LLM calls this turn.
        try:
            import uuid as _uuid

            from app.services.enterprise import credit_service as _cs

            in_tok = out_tok = 0
            for m in messages or []:
                um = getattr(m, "usage_metadata", None) or {}
                in_tok += int(um.get("input_tokens") or 0)
                out_tok += int(um.get("output_tokens") or 0)
            if in_tok == 0 and out_tok == 0:
                # Fallback estimate (~4 chars/token) when usage metadata is absent.
                out_tok = max(1, len(final_message) // 4)
                in_tok = len(message) // 4
            await _cs.record_ai_usage(
                _uuid.UUID(company_id),
                in_tok,
                out_tok,
                action="pilot_chat",
                user_id=getattr(current_user, "id", None),
                description="Croar Pilot chat",
            )
        except Exception:
            pass

        return {
            "response": final_message,
            "status": "success",
            "pilot_action": pilot_action,
            "metadata": result.get("metadata", {}),
        }
    except HTTPException:
        raise
    except Exception as e:
        # Keep the real error in the server logs; return a generic message to the client so we
        # don't leak internal exception detail (stack text, ids) into the chat UI.
        logger.exception("Croar Pilot chat failed")
        raise HTTPException(
            status_code=500,
            detail="Croar Pilot hit an unexpected error and couldn't complete that. Please try again.",
        ) from e
    finally:
        # Clear the live-progress marker so a finished/failed run doesn't leave a stale step showing.
        from app.agents.tools import PILOT_PROGRESS

        PILOT_PROGRESS.pop(thread_id, None)


@router.get("/pilot/progress")
async def pilot_progress(current_user: AuthUser, thread_id: str | None = None) -> dict[str, Any]:
    """The step the Pilot is CURRENTLY on for this thread (drives the working animation). Returns
    {"step": null} when idle. Namespaced by company so it matches what /chat wrote."""
    from app.agents.tools import PILOT_PROGRESS

    company_id = str(getattr(current_user, "company_id", "") or "")
    key = f"{company_id}:{thread_id or 'default_thread'}"
    return {"step": PILOT_PROGRESS.get(key)}


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
    count = max(1, min(payload.count, 25))
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

    # Resolve the job. The Pilot sometimes passes the ROLE NAME (e.g. "SAP") instead of the job's
    # UUID, so accept either: try it as a UUID first, then fall back to matching the job title within
    # the company. This is what previously 422'd every invite with a non-UUID job_id.
    from sqlalchemy import func

    job = None
    try:
        job_uuid = uuid.UUID(str(payload.job_id))
        job = (
            await session.execute(
                select(JobRequirement).where(
                    JobRequirement.id == job_uuid, JobRequirement.company_id == company_id
                )
            )
        ).scalar_one_or_none()
    except (ValueError, AttributeError):
        job = None
    if job is None:
        job = (
            (
                await session.execute(
                    select(JobRequirement)
                    .where(
                        func.lower(func.trim(JobRequirement.title)) == str(payload.job_id).strip().lower(),
                        JobRequirement.company_id == company_id,
                    )
                    .order_by(JobRequirement.created_at.desc())
                )
            )
            .scalars()
            .first()
        )
    if not job:
        raise HTTPException(status_code=404, detail="Couldn't find that job to send invites for.")

    # Always use the RESOLVED job id downstream (apply link, funnel tracking) — never the raw input,
    # which may have been a role name.
    resolved_job_id = str(job.id)
    # Public candidate-facing job page (has the apply form). NOT the /enterprise API path.
    apply_url = f"{settings.frontend_url}/jobs/{resolved_job_id}"
    sent, failed = 0, 0
    tracked: list[dict[str, Any]] = []  # persisted to the job's Sourcing funnel after the loop
    for c in payload.candidates:
        real_email = (c.email or "").strip()
        # A candidate with no real email is unreachable — count it as failed even in test mode
        # (otherwise the test-inbox redirect would report it as "sent" and overstate reach).
        if not real_email:
            failed += 1
            tracked.append(
                {
                    "full_name": c.name,
                    "email": None,
                    "platform": c.platform,
                    "profile_url": c.profile_url,
                    "headline": c.headline,
                    "location": c.location,
                    "invite_status": "failed",
                }
            )
            continue
        recipient = PILOT_TEST_EMAIL if PILOT_TEST_MODE else real_email
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
        invite_ok = False
        try:
            ok, err = await run_in_threadpool(send_smtp_email, recipient, subject, body, None, None)
            if ok:
                sent += 1
                invite_ok = True
            else:
                failed += 1
                logger.warning("Invite email failed for %s: %s", recipient, err)
        except Exception:
            failed += 1
            logger.exception("Invite email crashed")

        tracked.append(
            {
                "full_name": c.name,
                "email": real_email,
                "platform": c.platform,
                "profile_url": c.profile_url,
                "headline": c.headline,
                "location": c.location,
                "invite_status": "sent" if invite_ok else "failed",
            }
        )

    # Persist the invite funnel for the job's Sourcing tab (best-effort; never fails the request).
    try:
        from app.services.enterprise.sourcing import job_sourcing

        await run_in_threadpool(job_sourcing.record_invites, resolved_job_id, str(company_id or ""), tracked)
    except Exception:
        logger.exception("Failed to record sourcing invites")

    return {
        "status": "success",
        "sent": sent,
        "failed": failed,
        "test_mode": PILOT_TEST_MODE,
        "test_email": PILOT_TEST_EMAIL if PILOT_TEST_MODE else None,
    }
