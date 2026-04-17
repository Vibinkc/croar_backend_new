from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.company import Company
from app.models.enterprise.employee import Employee
from app.models.enterprise.project import Project, ProjectTask
from app.models.shared.constants import ModuleScope, PermissionAction
from app.router.enterprise.communication import send_smtp_email
from app.schemas.enterprise.projects import (
    ProjectCreate,
    ProjectMemberAdd,
    ProjectOut,
    ProjectTaskCreate,
    ProjectTaskOut,
    ProjectTaskUpdate,
    ProjectUpdate,
)

router = APIRouter(prefix="/projects", tags=["Enterprise Projects"])


@router.get("/", response_model=list[ProjectOut])
async def list_projects(
    session: DBSessionDep,
    current_user: Annotated[Any, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.read))],
):
    """List all projects for the organization."""
    stmt = (
        select(Project)
        .options(
            selectinload(Project.members), selectinload(Project.tasks).selectinload(ProjectTask.assignee)
        )
        .where(Project.company_id == current_user.company_id, Project.deleted_at == None)
        .order_by(Project.created_at.desc())
    )
    result = await session.execute(stmt)
    return result.scalars().all()


@router.post("/", response_model=ProjectOut, status_code=201)
async def create_project(
    request: ProjectCreate,
    session: DBSessionDep,
    current_user: Annotated[
        Any, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.create))
    ],
):
    """Create a new project."""
    data = request.model_dump()
    data["company_id"] = current_user.company_id
    project = Project(**data)
    session.add(project)
    await session.commit()

    # Reload with members
    stmt = (
        select(Project)
        .options(
            selectinload(Project.members), selectinload(Project.tasks).selectinload(ProjectTask.assignee)
        )
        .where(Project.id == project.id)
    )
    result = await session.execute(stmt)
    return result.scalar_one()


@router.get("/{id}", response_model=ProjectOut)
async def get_project(
    id: UUID,
    session: DBSessionDep,
    current_user: Annotated[Any, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.read))],
):
    """Get project details."""
    stmt = (
        select(Project)
        .options(
            selectinload(Project.members), selectinload(Project.tasks).selectinload(ProjectTask.assignee)
        )
        .where(Project.id == id, Project.company_id == current_user.company_id, Project.deleted_at == None)
    )
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
    current_user: Annotated[
        Any, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.update))
    ],
):
    """Update project details."""
    stmt = select(Project).where(
        Project.id == id, Project.company_id == current_user.company_id, Project.deleted_at == None
    )
    res = await session.execute(stmt)
    project = res.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    update_data = request.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(project, key, value)

    project.updated_at = datetime.now()
    await session.commit()

    # Reload with members
    stmt = (
        select(Project)
        .options(
            selectinload(Project.members), selectinload(Project.tasks).selectinload(ProjectTask.assignee)
        )
        .where(Project.id == project.id)
    )
    result = await session.execute(stmt)
    return result.scalar_one()


@router.delete("/{id}", status_code=204)
async def delete_project(
    id: UUID,
    session: DBSessionDep,
    current_user: Annotated[
        Any, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.delete))
    ],
):
    """Soft-delete a project."""
    stmt = select(Project).where(Project.id == id, Project.company_id == current_user.company_id)
    res = await session.execute(stmt)
    project = res.scalar_one_or_none()
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
    current_user: Annotated[
        Any, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.moderate))
    ],
):
    """Add an employee to a project."""
    stmt = (
        select(Project)
        .options(selectinload(Project.members))
        .where(Project.id == id, Project.company_id == current_user.company_id)
    )
    result = await session.execute(stmt)
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    stmt_emp = select(Employee).where(
        Employee.id == request.employee_id, Employee.company_id == current_user.company_id
    )
    res_emp = await session.execute(stmt_emp)
    employee = res_emp.scalar_one_or_none()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    if employee not in project.members:
        project.members.append(employee)
        await session.commit()

    # Reload with members and tasks
    stmt = (
        select(Project)
        .options(
            selectinload(Project.members), selectinload(Project.tasks).selectinload(ProjectTask.assignee)
        )
        .where(Project.id == id)
    )
    result = await session.execute(stmt)
    return result.scalar_one()


@router.delete("/{id}/members/{employee_id}", response_model=ProjectOut)
async def remove_project_member(
    id: UUID,
    employee_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[
        Any, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.moderate))
    ],
):
    """Remove an employee from a project."""
    stmt = (
        select(Project)
        .options(selectinload(Project.members))
        .where(Project.id == id, Project.company_id == current_user.company_id)
    )
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
    stmt = (
        select(Project)
        .options(
            selectinload(Project.members), selectinload(Project.tasks).selectinload(ProjectTask.assignee)
        )
        .where(Project.id == id)
    )
    result = await session.execute(stmt)
    return result.scalar_one()


# Task Management
@router.get("/tasks/all", response_model=list[ProjectTaskOut])
async def list_all_tasks(
    session: DBSessionDep,
    current_user: Annotated[Any, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.read))],
):
    """List all project tasks from all projects."""
    stmt = (
        select(ProjectTask)
        .join(Project)
        .options(selectinload(ProjectTask.project), selectinload(ProjectTask.assignee))
        .where(Project.company_id == current_user.company_id)
        .order_by(ProjectTask.created_at.desc())
    )
    result = await session.execute(stmt)
    return result.scalars().all()


@router.get("/{id}/tasks", response_model=list[ProjectTaskOut])
async def list_project_tasks(
    id: UUID,
    session: DBSessionDep,
    current_user: Annotated[Any, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.read))],
):
    """List all tasks for a project."""
    stmt = (
        select(ProjectTask)
        .join(Project)
        .options(selectinload(ProjectTask.assignee))
        .where(ProjectTask.project_id == id, Project.company_id == current_user.company_id)
    )
    result = await session.execute(stmt)
    return result.scalars().all()


@router.post("/{id}/tasks", response_model=ProjectTaskOut, status_code=201)
async def create_project_task(
    id: UUID,
    request: ProjectTaskCreate,
    session: DBSessionDep,
    background_tasks: BackgroundTasks,
    current_user: Annotated[
        Any, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.create))
    ],
):
    """Create a new task in a project and notify assignee."""
    stmt = select(Project).where(Project.id == id, Project.company_id == current_user.company_id)
    res_proj = await session.execute(stmt)
    project = res_proj.scalar_one_or_none()
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
            <p><strong>Due Date:</strong> {task.due_date or "No deadline"}</p>
            <p><strong>Description:</strong><br/>{task.description or "No description provided."}</p>
            <br/>
            <p>Please log in to the portal to view more details.</p>
            """
            background_tasks.add_task(
                run_in_threadpool, send_smtp_email, employee.email, subject, body, company_name
            )

    # Reload with assignee
    stmt = select(ProjectTask).options(selectinload(ProjectTask.assignee)).where(ProjectTask.id == task.id)
    result = await session.execute(stmt)
    return result.scalar_one()


@router.patch("/tasks/{task_id}", response_model=ProjectTaskOut)
async def update_project_task(
    task_id: UUID,
    request: ProjectTaskUpdate,
    session: DBSessionDep,
    current_user: Annotated[
        Any, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.update))
    ],
):
    """Update a task."""
    stmt = (
        select(ProjectTask)
        .join(Project)
        .where(ProjectTask.id == task_id, Project.company_id == current_user.company_id)
    )
    res = await session.execute(stmt)
    task = res.scalar_one_or_none()
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
    current_user: Annotated[
        Any, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.delete))
    ],
):
    """Delete a task."""
    stmt = (
        select(ProjectTask)
        .join(Project)
        .where(ProjectTask.id == task_id, Project.company_id == current_user.company_id)
    )
    res = await session.execute(stmt)
    task = res.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    await session.delete(task)
    await session.commit()
    return
