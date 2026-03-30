from typing import Annotated, List, Optional
from uuid import UUID
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy import select, update, delete
from sqlalchemy.orm import selectinload
from fastapi.concurrency import run_in_threadpool

from app.core.dependencies import DBSessionDep, get_current_agent
from app.models.enterprise.user_role import EnterpriseUser as HiringAgent
from app.models.enterprise.project import Project, project_members, ProjectTask
from app.models.enterprise.employee import Employee
from app.models.enterprise.company import Company
from app.schemas.enterprise.projects import (
    ProjectOut, ProjectCreate, ProjectUpdate, ProjectMemberAdd,
    ProjectTaskCreate, ProjectTaskOut, ProjectTaskUpdate
)
from app.router.enterprise.communication import send_smtp_email

router = APIRouter(prefix="/projects", tags=["Enterprise Projects"])

@router.get("/", response_model=List[ProjectOut])
async def list_projects(
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    """List all projects."""
    stmt = select(Project).options(
        selectinload(Project.members),
        selectinload(Project.tasks).selectinload(ProjectTask.assignee)
    ).where(Project.deleted_at == None).order_by(Project.created_at.desc())
    result = await session.execute(stmt)
    return result.scalars().all()

@router.post("/", response_model=ProjectOut, status_code=201)
async def create_project(
    request: ProjectCreate,
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    """Create a new project."""
    project = Project(**request.model_dump())
    session.add(project)
    await session.commit()
    
    # Reload with members
    stmt = select(Project).options(
        selectinload(Project.members),
        selectinload(Project.tasks).selectinload(ProjectTask.assignee)
    ).where(Project.id == project.id)
    result = await session.execute(stmt)
    return result.scalar_one()

@router.get("/{id}", response_model=ProjectOut)
async def get_project(
    id: UUID,
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    """Get project details."""
    stmt = select(Project).options(
        selectinload(Project.members),
        selectinload(Project.tasks).selectinload(ProjectTask.assignee)
    ).where(Project.id == id, Project.deleted_at == None)
    result = await session.execute(stmt)
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project

@router.patch("/{id}", response_model=ProjectOut)
async def update_project(
    id: UUID,
    request: ProjectUpdate,
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    """Update project details."""
    project = await session.get(Project, id)
    if not project or project.deleted_at:
        raise HTTPException(status_code=404, detail="Project not found")
    
    update_data = request.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(project, key, value)
    
    project.updated_at = datetime.now()
    await session.commit()
    
    # Reload with members
    stmt = select(Project).options(
        selectinload(Project.members),
        selectinload(Project.tasks).selectinload(ProjectTask.assignee)
    ).where(Project.id == project.id)
    result = await session.execute(stmt)
    return result.scalar_one()

@router.delete("/{id}", status_code=204)
async def delete_project(
    id: UUID,
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    """Soft-delete a project."""
    project = await session.get(Project, id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    project.deleted_at = datetime.now()
    await session.commit()
    return

# Member Management
@router.post("/{id}/members", response_model=ProjectOut)
async def add_project_member(
    id: UUID,
    request: ProjectMemberAdd,
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    """Add an employee to a project."""
    stmt = select(Project).options(selectinload(Project.members)).where(Project.id == id)
    result = await session.execute(stmt)
    project = result.scalar_one_or_none()
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    employee = await session.get(Employee, request.employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    
    if employee not in project.members:
        project.members.append(employee)
        await session.commit()
    
    # Reload with members and tasks
    stmt = select(Project).options(
        selectinload(Project.members),
        selectinload(Project.tasks).selectinload(ProjectTask.assignee)
    ).where(Project.id == id)
    result = await session.execute(stmt)
    return result.scalar_one()

@router.delete("/{id}/members/{employee_id}", response_model=ProjectOut)
async def remove_project_member(
    id: UUID,
    employee_id: UUID,
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    """Remove an employee from a project."""
    stmt = select(Project).options(selectinload(Project.members)).where(Project.id == id)
    result = await session.execute(stmt)
    project = result.scalar_one_or_none()
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    employee = await session.get(Employee, employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    
    if employee in project.members:
        project.members.remove(employee)
        await session.commit()
    
    # Reload with members and tasks
    stmt = select(Project).options(
        selectinload(Project.members),
        selectinload(Project.tasks).selectinload(ProjectTask.assignee)
    ).where(Project.id == id)
    result = await session.execute(stmt)
    return result.scalar_one()

# Task Management
@router.get("/tasks/all", response_model=List[ProjectTaskOut])
async def list_all_tasks(
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    """List all project tasks from all projects."""
    stmt = select(ProjectTask).options(
        selectinload(ProjectTask.project),
        selectinload(ProjectTask.assignee)
    ).order_by(ProjectTask.created_at.desc())
    result = await session.execute(stmt)
    return result.scalars().all()

@router.get("/{id}/tasks", response_model=List[ProjectTaskOut])
async def list_project_tasks(
    id: UUID,
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    """List all tasks for a project."""
    stmt = select(ProjectTask).options(selectinload(ProjectTask.assignee)).where(ProjectTask.project_id == id)
    result = await session.execute(stmt)
    return result.scalars().all()

@router.post("/{id}/tasks", response_model=ProjectTaskOut, status_code=201)
async def create_project_task(
    id: UUID,
    request: ProjectTaskCreate,
    session: DBSessionDep,
    background_tasks: BackgroundTasks,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    """Create a new task in a project and notify assignee."""
    project = await session.get(Project, id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    task = ProjectTask(project_id=id, **request.model_dump())
    session.add(task)
    await session.commit()
    await session.refresh(task)
    
    # Notify assignee if employee_id is provided
    if task.employee_id:
        employee = await session.get(Employee, task.employee_id)
        if employee and employee.email:
            company = await session.get(Company, project.company_id)
            company_name = company.name if company else "Croar"
            
            subject = f"New Task Assigned: {task.title}"
            body = f"""
            <h3>Hello {employee.first_name},</h3>
            <p>You have been assigned a new task in project <strong>{project.name}</strong>.</p>
            <p><strong>Task:</strong> {task.title}</p>
            <p><strong>Column:</strong> {task.column}</p>
            <p><strong>Due Date:</strong> {task.due_date or 'No deadline'}</p>
            <p><strong>Description:</strong><br/>{task.description or 'No description provided.'}</p>
            <br/>
            <p>Please log in to the portal to view more details.</p>
            """
            background_tasks.add_task(run_in_threadpool, send_smtp_email, employee.email, subject, body, company_name)
    
    # Reload with assignee
    stmt = select(ProjectTask).options(selectinload(ProjectTask.assignee)).where(ProjectTask.id == task.id)
    result = await session.execute(stmt)
    return result.scalar_one()

@router.patch("/tasks/{task_id}", response_model=ProjectTaskOut)
async def update_project_task(
    task_id: UUID,
    request: ProjectTaskUpdate,
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    """Update a task."""
    task = await session.get(ProjectTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    update_data = request.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(task, key, value)
    
    task.updated_at = datetime.now()
    await session.commit()
    
    # Reload with assignee
    stmt = select(ProjectTask).options(selectinload(ProjectTask.assignee)).where(ProjectTask.id == task.id)
    result = await session.execute(stmt)
    return result.scalar_one()

@router.delete("/tasks/{task_id}", status_code=204)
async def delete_project_task(
    task_id: UUID,
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)]
):
    """Delete a task."""
    task = await session.get(ProjectTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    await session.delete(task)
    await session.commit()
    return
