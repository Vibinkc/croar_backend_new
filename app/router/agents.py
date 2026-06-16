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

        return {"response": final_message, "status": "success", "metadata": result.get("metadata", {})}
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
