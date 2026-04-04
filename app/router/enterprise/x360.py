from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional
from uuid import UUID
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep, get_current_agent
from app.models.enterprise.user_role import EnterpriseUser as HiringAgent
from app.models.enterprise.x360 import (
    X360Question, X360AssessmentTemplate, X360TemplateQuestion,
    X360AssessmentCycle, X360AssessmentAssignment, X360AssessmentResponse,
    AssignmentStatus
)
from app.schemas.x360 import (
    X360Question as X360QuestionSchema, 
    X360QuestionCreate,
    X360AssessmentTemplate as X360AssessmentTemplateSchema, 
    X360AssessmentTemplateCreate,
    X360AssessmentCycle as X360AssessmentCycleSchema, 
    X360AssessmentCycleCreate,
    X360AssessmentAssignment as X360AssessmentAssignmentSchema, 
    X360AssessmentSubmit, X360Report,
    X360AIGenerateRequest, X360AIGeneratedQuestion
)
from app.services.x360_service import x360_service
from app.core.settings import settings
import openai
import json

router = APIRouter(prefix="/x360", tags=["Performance 360"])

# Questions
@router.post("/questions", response_model=X360QuestionSchema)
async def create_question(
    request: X360QuestionCreate,
    db: DBSessionDep,
    current_agent: HiringAgent = Depends(get_current_agent)
):
    # Determine company_id from agent or context
    # Assuming current_agent has company_id (based on model check)
    new_q = X360Question(**request.model_dump(), company_id=current_agent.company_id)
    db.add(new_q)
    await db.commit()
    await db.refresh(new_q)
    return new_q

@router.get("/questions", response_model=List[X360QuestionSchema])
async def list_questions(
    db: DBSessionDep,
    current_agent: HiringAgent = Depends(get_current_agent)
):
    stmt = select(X360Question).where(X360Question.company_id == current_agent.company_id)
    res = await db.execute(stmt)
    return res.scalars().all()

@router.post("/questions/ai-generate", response_model=List[X360AIGeneratedQuestion])
async def generate_questions_ai(
    request: X360AIGenerateRequest,
    current_agent: HiringAgent = Depends(get_current_agent)
):
    categories_str = ", ".join(request.categories)
    if request.custom_category:
        categories_str += f", {request.custom_category}"
        
    prompt = f"""You are an elite Organizational Psychologist. Generate {request.count} high-fidelity 360-degree feedback questions.
Categories to focus on: {categories_str}.
Industry Context: {request.additional_context or 'General Corporate'}

Requirements:
- Questions must be insightful, professional, and calibrated for a 360 assessment.
- Each question must be assigned one of the specified categories: {categories_str}.
- Ensure the questions reflect the nuances of the {request.additional_context or 'General Corporate'} industry.

Return ONLY a JSON object:
{{
  "questions": [
    {{
        "text": "The question text",
        "type": "RATING",
        "category": "one of the categories above"
    }},
    ...
  ]
}}
"""
    
    try:
        from app.core.ai import analyze_text_with_llm
        content = await analyze_text_with_llm(prompt)
        # Handle if AI wraps JSON in markdown blocks
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
            
        data = json.loads(content)
        # Extract questions from the nested field if present, otherwise assume data is the list
        questions_list = data.get("questions", data) if isinstance(data, dict) else data
        return questions_list if isinstance(questions_list, list) else []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Templates
@router.post("/templates", response_model=X360AssessmentTemplateSchema)
async def create_template(
    request: X360AssessmentTemplateCreate,
    db: DBSessionDep,
    current_agent: HiringAgent = Depends(get_current_agent)
):
    new_tpl = X360AssessmentTemplate(
        name=request.name,
        description=request.description,
        company_id=current_agent.company_id
    )
    db.add(new_tpl)
    await db.flush() # get ID

    for idx, q_id in enumerate(request.question_ids):
        tpl_q = X360TemplateQuestion(template_id=new_tpl.id, question_id=q_id, order=idx)
        db.add(tpl_q)
    
    await db.commit()
    # Refresh with relationships
    stmt = select(X360AssessmentTemplate).where(X360AssessmentTemplate.id == new_tpl.id).options(
        selectinload(X360AssessmentTemplate.questions).selectinload(X360TemplateQuestion.question)
    )
    res = await db.execute(stmt)
    return res.scalar_one()

@router.get("/templates", response_model=List[X360AssessmentTemplateSchema])
async def list_templates(
    db: DBSessionDep,
    current_agent: HiringAgent = Depends(get_current_agent)
):
    stmt = select(X360AssessmentTemplate).where(X360AssessmentTemplate.company_id == current_agent.company_id).options(
        selectinload(X360AssessmentTemplate.questions).selectinload(X360TemplateQuestion.question)
    )
    res = await db.execute(stmt)
    return res.scalars().all()

@router.put("/templates/{template_id}", response_model=X360AssessmentTemplateSchema)
async def update_template(
    template_id: UUID,
    request: X360AssessmentTemplateCreate,
    db: DBSessionDep,
    current_agent: HiringAgent = Depends(get_current_agent)
):
    stmt = select(X360AssessmentTemplate).where(
        X360AssessmentTemplate.id == template_id,
        X360AssessmentTemplate.company_id == current_agent.company_id
    )
    res = await db.execute(stmt)
    tpl = res.scalar_one_or_none()
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")
    
    tpl.name = request.name
    tpl.description = request.description
    
    # Sync questions
    from sqlalchemy import delete
    del_stmt = delete(X360TemplateQuestion).where(X360TemplateQuestion.template_id == template_id)
    await db.execute(del_stmt)
    
    for idx, q_id in enumerate(request.question_ids):
        tpl_q = X360TemplateQuestion(template_id=template_id, question_id=q_id, order=idx)
        db.add(tpl_q)
    
    await db.commit()
    
    # Refresh
    stmt = select(X360AssessmentTemplate).where(X360AssessmentTemplate.id == template_id).options(
        selectinload(X360AssessmentTemplate.questions).selectinload(X360TemplateQuestion.question)
    )
    res = await db.execute(stmt)
    return res.scalar_one()

@router.delete("/templates/{template_id}")
async def delete_template(
    template_id: UUID,
    db: DBSessionDep,
    current_agent: HiringAgent = Depends(get_current_agent)
):
    stmt = select(X360AssessmentTemplate).where(
        X360AssessmentTemplate.id == template_id,
        X360AssessmentTemplate.company_id == current_agent.company_id
    )
    res = await db.execute(stmt)
    tpl = res.scalar_one_or_none()
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")
    
    await db.delete(tpl)
    await db.commit()
    return {"status": "success"}

# Cycles
@router.post("/cycles", response_model=X360AssessmentCycleSchema)
async def create_and_start_cycle(
    request: X360AssessmentCycleCreate,
    db: DBSessionDep,
    current_agent: HiringAgent = Depends(get_current_agent)
):
    new_cycle = X360AssessmentCycle(
        name=request.name,
        start_date=request.start_date,
        end_date=request.end_date,
        template_id=request.template_id,
        company_id=current_agent.company_id,
        status="DRAFT"
    )
    db.add(new_cycle)
    await db.flush()
    
    # Generate assignments using service
    await x360_service.start_cycle(db, new_cycle.id, request.ratee_ids)
    
    return new_cycle

@router.get("/cycles/{cycle_id}/progress")
async def get_cycle_progress(
    cycle_id: UUID,
    db: DBSessionDep,
    current_agent: HiringAgent = Depends(get_current_agent)
):
    from app.models.enterprise.employee import Employee
    # Fetch all assignments in this cycle
    stmt = select(X360AssessmentAssignment).where(
        X360AssessmentAssignment.cycle_id == cycle_id
    ).options(selectinload(X360AssessmentAssignment.ratee))
    
    res = await db.execute(stmt)
    assignments = res.scalars().all()
    
    # Group by ratee
    progress_map = {}
    for ass in assignments:
        rid = str(ass.ratee_id)
        if rid not in progress_map:
            progress_map[rid] = {
                "ratee_id": ass.ratee_id,
                "ratee_name": f"{ass.ratee.first_name} {ass.ratee.last_name}",
                "total": 0,
                "completed": 0,
                "ai_score": None,
                "breakdown": []
            }
        
        progress_map[rid]["total"] += 1
        if ass.status == AssignmentStatus.COMPLETED:
            progress_map[rid]["completed"] += 1
            
        progress_map[rid]["breakdown"].append({
            "rater_relation": ass.relation,
            "status": ass.status
        })
    
    # Calculate AI scores for completed ratees
    for rid, data in progress_map.items():
        if data["total"] > 0 and data["completed"] == data["total"]:
            # Fetch report data (which includes AI eval)
            report = await x360_service.get_report(db, data["ratee_id"], cycle_id)
            if report and report.get("ai_evaluation"):
                data["ai_score"] = report["ai_evaluation"].get("score")
        
    return list(progress_map.values())

@router.get("/cycles", response_model=List[X360AssessmentCycleSchema])
async def list_cycles(
    db: DBSessionDep,
    current_agent: HiringAgent = Depends(get_current_agent)
):
    stmt = select(X360AssessmentCycle).where(X360AssessmentCycle.company_id == current_agent.company_id)
    res = await db.execute(stmt)
    return res.scalars().all()

# Assignments for current user
@router.get("/my-assessments", response_model=List[X360AssessmentAssignmentSchema])
async def get_my_assessments(
    db: DBSessionDep,
    current_agent: HiringAgent = Depends(get_current_agent)
):
    # Need to find the Employee ID associated with current_agent.email
    # Assuming logic where EnterpriseUser.email matches Employee.email
    from app.models.enterprise.employee import Employee
    emp_stmt = select(Employee).where(Employee.email == current_agent.email)
    emp_res = await db.execute(emp_stmt)
    emp = emp_res.scalar_one_or_none()
    if not emp:
        return []

    stmt = select(X360AssessmentAssignment).where(
        X360AssessmentAssignment.rater_id == emp.id
    ).options(
        selectinload(X360AssessmentAssignment.ratee),
        selectinload(X360AssessmentAssignment.rater)
    )
    res = await db.execute(stmt)
    return res.scalars().all()

@router.get("/assessments/{assignment_id}")
async def get_assessment_details(
    assignment_id: UUID,
    db: DBSessionDep,
    current_agent: HiringAgent = Depends(get_current_agent)
):
    stmt = select(X360AssessmentAssignment).where(
        X360AssessmentAssignment.id == assignment_id
    ).options(
        selectinload(X360AssessmentAssignment.ratee),
        selectinload(X360AssessmentAssignment.cycle).selectinload(X360AssessmentCycle.template)
    )
    res = await db.execute(stmt)
    assignment = res.scalar_one_or_none()
    
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
        
    # Fetch template separately to get questions
    tpl_stmt = select(X360AssessmentTemplate).where(
        X360AssessmentTemplate.id == assignment.cycle.template_id
    ).options(
        selectinload(X360AssessmentTemplate.questions).selectinload(X360TemplateQuestion.question)
    )
    tpl_res = await db.execute(tpl_stmt)
    template = tpl_res.scalar_one_or_none()
    
    return {
        "assignment": assignment,
        "template": template
    }

@router.post("/assessments/{assignment_id}/submit")
async def submit_assessment(
    assignment_id: UUID,
    request: X360AssessmentSubmit,
    db: DBSessionDep,
    current_agent: Optional[HiringAgent] = Depends(get_current_agent) # Optional for portal
):
    assign_stmt = select(X360AssessmentAssignment).where(X360AssessmentAssignment.id == assignment_id)
    res = await db.execute(assign_stmt)
    assignment = res.scalar_one_or_none()
    
    if not assignment or assignment.status == AssignmentStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Assignment already completed or not found")

    for resp_data in request.responses:
        resp = X360AssessmentResponse(
            assignment_id=assignment_id,
            question_id=resp_data.question_id,
            answer_value=resp_data.answer_value,
            answer_text=resp_data.answer_text
        )
        db.add(resp)
    
    assignment.status = AssignmentStatus.COMPLETED
    assignment.completed_at = datetime.now()
    await db.commit()
    return {"status": "success"}

# Portal (Public Access)
@router.post("/portal/login")
async def portal_login(
    employee_id: UUID,
    email: str,
    db: DBSessionDep
):
    from app.models.enterprise.employee import Employee
    emp_stmt = select(Employee).where(
        Employee.id == employee_id,
        Employee.email == email
    )
    emp_res = await db.execute(emp_stmt)
    emp = emp_res.scalar_one_or_none()
    
    if not emp:
        raise HTTPException(status_code=401, detail="Invalid Employee ID or Email")
        
    stmt = select(X360AssessmentAssignment).where(
        X360AssessmentAssignment.rater_id == emp.id,
        X360AssessmentAssignment.status == AssignmentStatus.PENDING
    ).options(
        selectinload(X360AssessmentAssignment.ratee),
        selectinload(X360AssessmentAssignment.cycle)
    )
    res = await db.execute(stmt)
    assignments = res.scalars().all()
    
    return {
        "employee": emp,
        "assignments": assignments
    }

@router.get("/portal/assessments/{assignment_id}")
async def get_portal_assessment_details(
    assignment_id: UUID,
    db: DBSessionDep
):
    # Public but requires valid assignment ID
    stmt = select(X360AssessmentAssignment).where(
        X360AssessmentAssignment.id == assignment_id
    ).options(
        selectinload(X360AssessmentAssignment.ratee),
        selectinload(X360AssessmentAssignment.cycle).selectinload(X360AssessmentCycle.template)
    )
    res = await db.execute(stmt)
    assignment = res.scalar_one_or_none()
    
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
        
    tpl_stmt = select(X360AssessmentTemplate).where(
        X360AssessmentTemplate.id == assignment.cycle.template_id
    ).options(
        selectinload(X360AssessmentTemplate.questions).selectinload(X360TemplateQuestion.question)
    )
    tpl_res = await db.execute(tpl_stmt)
    template = tpl_res.scalar_one_or_none()
    
    return {
        "assignment": assignment,
        "template": template
    }

# Reports
@router.get("/reports/{employee_id}/{cycle_id}", response_model=X360Report)
async def get_360_report(
    employee_id: UUID,
    cycle_id: UUID,
    db: DBSessionDep,
    current_agent: HiringAgent = Depends(get_current_agent)
):
    report = await x360_service.get_report(db, employee_id, cycle_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return report

@router.get("/portal/assignments-by-rater/{rater_id}")
async def get_rater_assignments(
    rater_id: UUID,
    db: DBSessionDep
):
    stmt = select(X360AssessmentAssignment).where(
        and_(
            X360AssessmentAssignment.rater_id == rater_id,
            X360AssessmentAssignment.status == AssignmentStatus.PENDING
        )
    ).options(
        selectinload(X360AssessmentAssignment.ratee),
        selectinload(X360AssessmentAssignment.cycle)
    )
    res = await db.execute(stmt)
    assignments = res.scalars().all()
    # Manual serialization since it includes models with relations
    return [{
        "id": str(ass.id),
        "relation": ass.relation,
        "ratee": {
            "first_name": ass.ratee.first_name,
            "last_name": ass.ratee.last_name
        },
        "cycle": {
            "name": ass.cycle.name
        }
    } for ass in assignments]
