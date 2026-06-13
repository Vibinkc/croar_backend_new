from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.shared.agents import AgentAction, ApprovalRequest

router = APIRouter(prefix="/agents", tags=["Agent OS"])

# Require an authenticated user for the agent audit-log / approval endpoints, which
# previously exposed AI action history and pending approvals with no auth at all.
AuthUser = Annotated[object, Depends(get_current_user)]


class AgentChatRequest(BaseModel):
    message: str
    thread_id: str = "default_thread"
    context: str = "general"
    metadata: dict[str, Any] = {}


from langchain_core.messages import HumanMessage

from app.agents.agent import hr_agent_executor


@router.post("/chat")
async def agent_chat(request: AgentChatRequest, session: AsyncSession = Depends(get_db)):
    """
    Primary endpoint for interacting with the AI HR Copilot.
    Executes the LangGraph Agentic workflow with state persistence.
    """
    try:
        # Prepare the input for the graph
        # LangGraph will automatically load the state for the given thread_id
        # and append the new message to the history.
        inputs = {"messages": [HumanMessage(content=request.message)]}

        # Configuration for the graph execution (thread_id for persistence, session for tools)
        config = {"configurable": {"thread_id": request.thread_id, "session": session}}

        # Execute the graph (it resumes from the last state in the thread)
        result = await hr_agent_executor.ainvoke(inputs, config=config)

        # Get the last message from the agent
        final_message = result["messages"][-1].content

        return {"response": final_message, "status": "success", "metadata": result.get("metadata", {})}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


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
