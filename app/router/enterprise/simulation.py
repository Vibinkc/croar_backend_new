import json
from typing import List, Optional
from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, delete, update
from sqlalchemy.orm import selectinload

from jose import jwt
from app.core.settings import get_settings
from app.core.dependencies import DBSessionDep, get_current_agent, oauth2_scheme
from app.models.enterprise.simulation import SimulationScenario, SimulationSession, SimulationAssignment
from app.models.enterprise.employee import Employee
from app.models.enterprise.hiring_agent import HiringAgent
from app.schemas.simulation import (
    SimulationScenarioSchema,
    SimulationScenarioCreate,
    SimulationScenarioUpdate,
    SimulationSessionSchema,
    SimulationSessionCreate,
    SimulationChatMessage,
    SimulationChatResponse,
    AIGenerateScenarioRequest,
    SimulationAssignmentCreate,
    SimulationAssignmentSchema,
    SimulationResultSchema
)
from app.core.ai import analyze_text_with_llm
from fastapi import BackgroundTasks
from fastapi.concurrency import run_in_threadpool
from app.router.enterprise.communication import send_smtp_email

router = APIRouter()
_settings = get_settings()

async def get_current_agent_optional(
    db: DBSessionDep,
    request: Request
) -> Optional[HiringAgent]:
    """Optional version of get_current_agent for polymorphic portal access."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    
    token = auth_header.split(" ")[1]
    try:
        payload = jwt.decode(token, _settings.secret_key, algorithms=[_settings.algorithm])
        email: str = payload.get("sub")
        if not email:
            return None
        stmt = select(HiringAgent).where(HiringAgent.email == email)
        res = await db.execute(stmt)
        return res.scalar_one_or_none()
    except:
        return None

# Scenarios Management (Admin)
@router.post("/scenarios", response_model=SimulationScenarioSchema)
async def create_scenario(
    request: SimulationScenarioCreate,
    db: DBSessionDep,
    current_agent: HiringAgent = Depends(get_current_agent)
):
    new_sc = SimulationScenario(
        **request.model_dump(),
        company_id=current_agent.company_id
    )
    db.add(new_sc)
    await db.commit()
    await db.refresh(new_sc)
    return new_sc

@router.get("/scenarios", response_model=List[SimulationScenarioSchema])
async def list_scenarios(
    db: DBSessionDep,
    current_agent: HiringAgent = Depends(get_current_agent)
):
    stmt = select(SimulationScenario).where(SimulationScenario.company_id == current_agent.company_id)
    res = await db.execute(stmt)
    return res.scalars().all()

@router.put("/scenarios/{scenario_id}", response_model=SimulationScenarioSchema)
async def update_scenario(
    scenario_id: UUID,
    request: SimulationScenarioUpdate,
    db: DBSessionDep,
    current_agent: HiringAgent = Depends(get_current_agent)
):
    stmt = select(SimulationScenario).where(
        SimulationScenario.id == scenario_id,
        SimulationScenario.company_id == current_agent.company_id
    )
    res = await db.execute(stmt)
    sc = res.scalar_one_or_none()
    if not sc:
        raise HTTPException(status_code=404, detail="Scenario not found")
    
    update_data = request.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        setattr(sc, k, v)
    
    db.add(sc)
    await db.commit()
    await db.refresh(sc)
    return sc

@router.delete("/scenarios/{scenario_id}")
async def delete_scenario(
    scenario_id: UUID,
    db: DBSessionDep,
    current_agent: HiringAgent = Depends(get_current_agent)
):
    stmt = delete(SimulationScenario).where(
        SimulationScenario.id == scenario_id,
        SimulationScenario.company_id == current_agent.company_id
    )
    await db.execute(stmt)
    await db.commit()
    return {"status": "success"}

@router.post("/scenarios/ai-generate", response_model=SimulationScenarioCreate)
async def ai_generate_scenario(
    request: AIGenerateScenarioRequest,
    current_agent: HiringAgent = Depends(get_current_agent)
):
    prompt = f"""You are an elite Organizational Psychologist and HR Training Expert. 
Your goal is to blueprint a high-fidelity behavioral role-play scenario for an employee.

User Request: {request.prompt}

Return ONLY a JSON object that matches this structure:
{{
  "title": "A short, professional title for the scenario",
  "description": "A brief overview of the learning objectives",
  "category": "CONFLICT / SALES / LEADERSHIP / CUSTOMER_SERVICE / EXIT_INTERVIEW",
  "character_name": "A personality-rich name for the AI Agent",
  "character_role": "The professional role of the character",
  "system_prompt": "DEEP NEURAL LOGIC: Full instructions for the AI on how to behave. Include personality traits, specific emotional triggers related to the scenario, and the desired outcome (e.g. 'Be firm but fair', 'Start angry but calm down if the employee uses empathy').",
  "initial_message": "The character's first line in the simulation",
  "difficulty": "Beginner / Intermediate / Advanced"
}}
"""
    try:
        response_str = await analyze_text_with_llm(prompt)
        
        # Standardize LLM output clean-up
        if "```json" in response_str:
            response_str = response_str.split("```json")[1].split("```")[0].strip()
        elif "```" in response_str:
            response_str = response_str.split("```")[1].split("```")[0].strip()
            
        data = json.loads(response_str)
        return SimulationScenarioCreate(**data)
    except Exception as e:
        print(f"Scenario Generation Error: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate scenario blueprint")

# Assignment Management (Admin)
@router.post("/assignments", response_model=List[SimulationAssignmentSchema])
async def create_assignments(
    request: SimulationAssignmentCreate,
    db: DBSessionDep,
    background_tasks: BackgroundTasks,
    current_agent: HiringAgent = Depends(get_current_agent)
):
    # Fetch scenario details for the email
    sc_stmt = select(SimulationScenario).where(SimulationScenario.id == request.scenario_id)
    sc_res = await db.execute(sc_stmt)
    scenario = sc_res.scalar_one_or_none()
    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found")

    assignments = []
    for emp_id in request.employee_ids:
        # Fetch employee details for personalization
        emp_stmt = select(Employee).where(Employee.id == emp_id)
        emp_res = await db.execute(emp_stmt)
        emp = emp_res.scalar_one_or_none()
        
        if not emp:
            continue

        new_assign = SimulationAssignment(
            scenario_id=request.scenario_id,
            employee_id=emp_id,
            due_date=request.due_date,
            status="PENDING"
        )
        db.add(new_assign)
        assignments.append(new_assign)

        # Queue Email Notification
        subject = f"[Action Required] New AI Training Assigned: {scenario.title}"
        body = f"""
        <p>Hi {emp.first_name},</p>
        <p>A new interactive AI simulation has been assigned to you in the <strong>Neural Coaching Lab</strong>:</p>
        <div style="background: #f8fafc; padding: 20px; border-radius: 12px; margin: 20px 0; border: 1px solid #e2e8f0;">
            <p style="margin: 0; font-weight: bold; color: #4f46e5;">Scenario: {scenario.title}</p>
            <p style="margin: 5px 0 0 0; color: #64748b; font-size: 14px;">Character: {scenario.character_name} ({scenario.character_role})</p>
        </div>
        <p>This simulation is designed to help you practice real-world behavioral challenges in a safe, immersive environment.</p>
        <p>Please log in to your employee portal to engage the simulation.</p>
        """
        background_tasks.add_task(run_in_threadpool, send_smtp_email, emp.email, subject, body, "Croar Lab")
    
    await db.commit()
    
    # Re-fetch with selectinload to avoid lazy-loading issues in schema serialization
    assign_ids = [a.id for a in assignments]
    stmt = (
        select(SimulationAssignment)
        .where(SimulationAssignment.id.in_(assign_ids))
        .options(selectinload(SimulationAssignment.scenario))
    )
    res = await db.execute(stmt)
    return res.scalars().all()

@router.get("/assignments/me", response_model=List[SimulationAssignmentSchema])
async def list_my_assignments(
    db: DBSessionDep,
    current_agent: HiringAgent = Depends(get_current_agent)
):
    # Determine if logged-in user is an employee
    emp_stmt = select(Employee).where(Employee.email == current_agent.email)
    emp_res = await db.execute(emp_stmt)
    employee = emp_res.scalar_one_or_none()
    
    if not employee:
        # Check if the hiring agent themselves has assignments (rare but possible if mirrored)
        return []
        
    stmt = select(SimulationAssignment).where(
        SimulationAssignment.employee_id == employee.id,
        SimulationAssignment.status != "COMPLETED"
    ).options(selectinload(SimulationAssignment.scenario))
    res = await db.execute(stmt)
    return res.scalars().all()

# Session Management (Employee/Admin)
@router.post("/sessions", response_model=SimulationSessionSchema)
async def start_session(
    request: SimulationSessionCreate,
    db: DBSessionDep,
    current_agent: Optional[HiringAgent] = Depends(get_current_agent_optional)
):
    # Verify scenario
    sc_stmt = select(SimulationScenario).where(SimulationScenario.id == request.scenario_id)
    sc_res = await db.execute(sc_stmt)
    scenario = sc_res.scalar_one_or_none()
    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found")
        
    # Start with initial message
    initial_conv = [
        {"role": "system", "content": scenario.system_prompt},
        {"role": "assistant", "content": scenario.initial_message}
    ]
    
    # Identify User Identity for Attribution
    # If employee_id is explicitly passed (Admin starting for employee)
    # or if we need to derive it from the logged-in user.
    session_user_id = request.employee_id
    hiring_agent_id = None
    
    if not session_user_id and current_agent:
        # Check if the logged-in agent is actually an employee
        emp_stmt = select(Employee).where(Employee.email == current_agent.email)
        emp_res = await db.execute(emp_stmt)
        employee = emp_res.scalar_one_or_none()
        if employee:
            session_user_id = employee.id
        else:
            # Fallback to Hiring Agent (Admin/Recruiter taking simulation for testing)
            hiring_agent_id = current_agent.id
    elif not session_user_id and not current_agent:
        raise HTTPException(status_code=401, detail="Authentication required to start session")

    new_sess = SimulationSession(
        employee_id=session_user_id,
        hiring_agent_id=hiring_agent_id,
        scenario_id=request.scenario_id,
        assignment_id=request.assignment_id,
        conversation=initial_conv,
        status="ONGOING"
    )
    
    # Update assignment status if linked
    if request.assignment_id:
        await db.execute(
            update(SimulationAssignment)
            .where(SimulationAssignment.id == request.assignment_id)
            .values(status="IN_PROGRESS")
        )
        
    db.add(new_sess)
    await db.commit()
    
    # Re-fetch with selectinload for scenario to avoid serialization error
    stmt = select(SimulationSession).where(SimulationSession.id == new_sess.id).options(
        selectinload(SimulationSession.scenario)
    )
    res = await db.execute(stmt)
    return res.scalar_one()

@router.get("/sessions/{session_id}", response_model=SimulationSessionSchema)
async def get_session(
    session_id: UUID,
    db: DBSessionDep,
    current_agent: Optional[HiringAgent] = Depends(get_current_agent_optional)
):
    stmt = select(SimulationSession).where(SimulationSession.id == session_id).options(
        selectinload(SimulationSession.scenario)
    )
    res = await db.execute(stmt)
    sess = res.scalar_one_or_none()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    return sess

@router.post("/sessions/{session_id}/chat", response_model=SimulationChatResponse)
async def simulation_chat(
    session_id: UUID,
    payload: SimulationChatMessage,
    db: DBSessionDep,
    current_agent: Optional[HiringAgent] = Depends(get_current_agent_optional)
):
    stmt = select(SimulationSession).where(SimulationSession.id == session_id).options(
        selectinload(SimulationSession.scenario)
    )
    res = await db.execute(stmt)
    sess = res.scalar_one_or_none()
    if not sess or sess.status != "ONGOING":
        raise HTTPException(status_code=400, detail="Session not ongoing")
        
    history = list(sess.conversation)
    history.append({"role": "user", "content": payload.message})
    
    chat_context = "\n".join([f"{m['role']}: {m['content']}" for m in history])
    ai_reply = await analyze_text_with_llm(f"Respond to the following conversation as the character {sess.scenario.character_name}. KEEP IT BRIEF, REALISTIC, AND HIGH-FIDELITY. DO NOT include JSON brackets or any other formatting. Just return the character's direct spoken response.\n\n{chat_context}")
    
    actual_reply = ai_reply
    try:
        # Clear common AI artifacts like markdown code blocks
        if "```" in ai_reply:
             ai_reply = ai_reply.split("```")[1]
             if ai_reply.startswith("json"):
                 ai_reply = ai_reply[4:]
             ai_reply = ai_reply.split("```")[0].strip()

        reply_data = json.loads(ai_reply)
        if isinstance(reply_data, dict):
            # Try to find 'reply' or use the first value in the dictionary
            actual_reply = reply_data.get("reply")
            if not actual_reply:
                actual_reply = next(iter(reply_data.values()), ai_reply)
    except:
        actual_reply = ai_reply

    history.append({"role": "assistant", "content": str(actual_reply)})
    sess.conversation = history
    db.add(sess)
    await db.commit()
    
    return SimulationChatResponse(reply=actual_reply, status="ONGOING")

@router.post("/sessions/{session_id}/complete", response_model=SimulationSessionSchema)
async def complete_simulation(
    session_id: UUID,
    db: DBSessionDep,
    current_agent: Optional[HiringAgent] = Depends(get_current_agent_optional)
):
    stmt = select(SimulationSession).where(SimulationSession.id == session_id).options(
        selectinload(SimulationSession.scenario)
    )
    res = await db.execute(stmt)
    sess = res.scalar_one_or_none()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
        
    # SUBSTANCE CHECK: ensure sufficient interaction before triggering LLM evaluation
    user_messages = [m for m in sess.conversation if m.role == "user"]
    if len(user_messages) < 3:
        raise HTTPException(
            status_code=400, 
            detail="INSUFFICIENT_INTERACTION: Please engage in at least 3 exchanges with the AI Agent before requesting a behavioral audit."
        )

    chat_context = "\n".join([f"{m['role']}: {m['content']}" for m in sess.conversation])
    eval_prompt = f"""You are an elite Performance Coach and Behavioral Psychologist. 
Analyze this role-play simulation:
Scenario: {sess.scenario.title}
Character: {sess.scenario.character_name}

CRITICAL: If the conversation is extremely short or the user is clearly not engaging seriously (e.g. just saying 'hi', 'ok'), set 'overall_score' to 0 and state that more data is needed in 'coaching_summary'.

Return ONLY a JSON object:
{{
  "communication_score": (1-10),
  "empathy_score": (1-10),
  "problem_solving_score": (1-10),
  "overall_score": (1-10 or 0 for insufficient engagement),
  "strengths": ["...", "..."],
  "areas_for_improvement": ["...", "..."],
  "coaching_summary": "..."
}}
"""
    eval_str = await analyze_text_with_llm(eval_prompt)
    try:
        if "```json" in eval_str:
            eval_str = eval_str.split("```json")[1].split("```")[0].strip()
        report_data = json.loads(eval_str)
        sess.report = report_data
        sess.overall_score = report_data.get("overall_score", 0)
        sess.feedback = report_data.get("coaching_summary", "")
    except Exception as e:
        sess.report = {"error": "Failed to generate evaluation"}
        
    sess.status = "COMPLETED"
    sess.completed_at = datetime.now()
    
    # Update assignment if linked
    if sess.assignment_id:
        await db.execute(
            update(SimulationAssignment)
            .where(SimulationAssignment.id == sess.assignment_id)
            .values(status="COMPLETED", completed_at=datetime.now())
        )
        
    db.add(sess)
    await db.commit()
    await db.refresh(sess)
    return sess

@router.get("/results", response_model=List[SimulationResultSchema])
async def list_simulation_results(
    db: DBSessionDep,
    current_agent: HiringAgent = Depends(get_current_agent)
):
    """Admin Dashboard view for all simulation results."""
    stmt = select(SimulationSession).where(
        SimulationSession.status == "COMPLETED"
    ).options(
        selectinload(SimulationSession.scenario),
        selectinload(SimulationSession.employee)
    ).order_by(SimulationSession.completed_at.desc())
    
    res = await db.execute(stmt)
    sessions = res.scalars().all()
    
    results = []
    for s in sessions:
        results.append({
            "id": s.id,
            "employee_name": f"{s.employee.first_name} {s.employee.last_name}" if s.employee else "Admin / Guest",
            "scenario_title": s.scenario.title,
            "category": s.scenario.category,
            "status": s.status,
            "overall_score": float(s.overall_score) if s.overall_score else None,
            "created_at": s.created_at,
            "completed_at": s.completed_at
        })
    return results
