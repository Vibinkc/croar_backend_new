from typing import Annotated, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy import select
from app.core.dependencies import DBSessionDep, get_current_agent
from app.models.enterprise.user_role import EnterpriseUser as HiringAgent
from app.models.enterprise.candidate import Candidate, CandidateApplication
from app.models.enterprise.job import JobRequirement
from app.services.enterprise.hiring_agent import hiring_agent_service

router = APIRouter(prefix="/agent", tags=["Hiring Agent"])

async def get_enterprise_agent(current_agent: Annotated[HiringAgent, Depends(get_current_agent)]) -> HiringAgent:
    return current_agent

@router.post("/process-all")
async def process_active_candidates(
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_enterprise_agent)],
    background_tasks: BackgroundTasks
):
    """
    Manually trigger the AI Agent to process all pending applications.
    """
    stmt = select(CandidateApplication).where(
        CandidateApplication.deleted_at == None
    )
    result = await session.execute(stmt)
    applications = result.scalars().all()
    
    results = []
    for app in applications:
        res = await hiring_agent_service.process_application(str(app.id), session, background_tasks)
        results.append({"application_id": app.id, "result": res})
        
    return {
        "status": "success",
        "processed_count": len(results),
        "details": results
    }

@router.post("/inbound-email")
async def handle_inbound_email(
    request: Dict[str, Any],
    session: DBSessionDep,
    background_tasks: BackgroundTasks
):
    """
    Webhook endpoint to receive incoming emails.
    """
    from_email = request.get("from")
    subject = request.get("subject")
    body = request.get("body") or request.get("text") or request.get("html")
    
    if not from_email or not subject or not body:
        raise HTTPException(status_code=400, detail="Missing email components")
        
    result = await hiring_agent_service.process_inbound_email(from_email, subject, body, session, background_tasks)
    return result

@router.get("/application/{application_id}/log")
async def get_application_agent_log(
    application_id: str,
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_enterprise_agent)]
):
    """Retrieve the AI Agent's activity log for a specific application."""
    app = await session.get(CandidateApplication, application_id)
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
        
    return (app.ai_feedback or {}).get("agent_log", [])
