from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.project import Project, ProjectTask
from app.models.shared.constants import ModuleScope, PermissionAction
from app.schemas.enterprise.projects import (
    ProjectCreate,
    ProjectMemberAdd,
    ProjectTaskCreate,
    ProjectTaskUpdate,
    ProjectUpdate,
)
from app.schemas.enterprise.projects import ProjectOut as ProjectSchema
from app.schemas.enterprise.projects import ProjectTaskOut as ProjectTaskSchema

router = APIRouter(prefix="/projects", tags=["Projects"])


@router.post("/", response_model=ProjectSchema)
async def create_project(
    request: ProjectCreate,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.projects, PermissionAction.create))
    ],
) -> Project:
    new_project = Project(
        name=request.name,
        description=request.description,
        status=request.status,
        start_date=request.start_date,
        end_date=request.end_date,
        company_id=cast("UUID", getattr(current_user, "company_id", None)),
    )
    db.add(new_project)
    await db.commit()

    # Reload with relations
    stmt = (
        select(Project)
        .where(Project.id == new_project.id)
        .options(
            selectinload(Project.tasks).selectinload(ProjectTask.assignee),
            selectinload(Project.tasks).selectinload(ProjectTask.project),
            selectinload(Project.members),
        )
    )
    res = await db.execute(stmt)
    return res.scalar_one()


@router.get("/", response_model=list[ProjectSchema])
async def list_projects(
    db: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.projects, PermissionAction.read))],
) -> list[Project]:
    stmt = (
        select(Project)
        .where(Project.company_id == getattr(current_user, "company_id", None))
        .options(
            selectinload(Project.tasks).selectinload(ProjectTask.assignee),
            selectinload(Project.tasks).selectinload(ProjectTask.project),
            selectinload(Project.members),
        )
    )
    res = await db.execute(stmt)
    return list(res.scalars().all())


@router.get("/{project_id}", response_model=ProjectSchema)
async def get_project(
    project_id: UUID,
    db: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.projects, PermissionAction.read))],
) -> Project:
    stmt = (
        select(Project)
        .where(Project.id == project_id, Project.company_id == getattr(current_user, "company_id", None))
        .options(
            selectinload(Project.tasks).selectinload(ProjectTask.assignee),
            selectinload(Project.tasks).selectinload(ProjectTask.project),
            selectinload(Project.members),
        )
    )
    res = await db.execute(stmt)
    project = res.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.put("/{project_id}", response_model=ProjectSchema)
async def update_project(
    project_id: UUID,
    request: ProjectUpdate,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.projects, PermissionAction.update))
    ],
) -> Project:
    stmt = select(Project).where(
        Project.id == project_id, Project.company_id == getattr(current_user, "company_id", None)
    )
    res = await db.execute(stmt)
    project = res.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    update_data = request.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(project, key, value)

    await db.commit()

    # Reload with relations
    stmt_reload = (
        select(Project)
        .where(Project.id == project.id)
        .options(
            selectinload(Project.tasks).selectinload(ProjectTask.assignee),
            selectinload(Project.tasks).selectinload(ProjectTask.project),
            selectinload(Project.members),
        )
    )
    res_reload = await db.execute(stmt_reload)
    return res_reload.scalar_one()


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: UUID,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.projects, PermissionAction.delete))
    ],
) -> None:
    stmt = select(Project).where(
        Project.id == project_id, Project.company_id == getattr(current_user, "company_id", None)
    )
    res = await db.execute(stmt)
    project = res.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    await db.delete(project)
    await db.commit()
    return


@router.post("/{project_id}/members", response_model=ProjectSchema)
async def add_project_member(
    project_id: UUID,
    request: ProjectMemberAdd,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.projects, PermissionAction.update))
    ],
) -> Project:
    from app.models.enterprise.employee import Employee

    stmt = (
        select(Project)
        .where(Project.id == project_id, Project.company_id == getattr(current_user, "company_id", None))
        .options(selectinload(Project.members))
    )
    res = await db.execute(stmt)
    project = res.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    emp_stmt = select(Employee).where(
        Employee.id == request.employee_id, Employee.company_id == project.company_id
    )
    emp_res = await db.execute(emp_stmt)
    employee = emp_res.scalar_one_or_none()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    if employee not in project.members:
        project.members.append(employee)
        await db.commit()

    # Reload with all relations
    stmt_reload = (
        select(Project)
        .where(Project.id == project.id)
        .options(
            selectinload(Project.tasks).selectinload(ProjectTask.assignee),
            selectinload(Project.tasks).selectinload(ProjectTask.project),
            selectinload(Project.members),
        )
    )
    res_reload = await db.execute(stmt_reload)
    return res_reload.scalar_one()


@router.delete("/{project_id}/members/{employee_id}", response_model=ProjectSchema)
async def remove_project_member(
    project_id: UUID,
    employee_id: UUID,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.projects, PermissionAction.update))
    ],
) -> Project:
    stmt = (
        select(Project)
        .where(Project.id == project_id, Project.company_id == getattr(current_user, "company_id", None))
        .options(selectinload(Project.members))
    )
    res = await db.execute(stmt)
    project = res.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    project.members = [m for m in project.members if m.id != employee_id]
    await db.commit()

    # Reload with all relations
    stmt_reload = (
        select(Project)
        .where(Project.id == project.id)
        .options(
            selectinload(Project.tasks).selectinload(ProjectTask.assignee),
            selectinload(Project.tasks).selectinload(ProjectTask.project),
            selectinload(Project.members),
        )
    )
    res_reload = await db.execute(stmt_reload)
    return res_reload.scalar_one()


# Task Management
@router.post("/{project_id}/tasks", response_model=ProjectTaskSchema)
async def create_project_task(
    project_id: UUID,
    request: ProjectTaskCreate,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.projects, PermissionAction.create))
    ],
) -> ProjectTask:
    # Verify project ownership
    stmt_proj = select(Project).where(
        Project.id == project_id, Project.company_id == getattr(current_user, "company_id", None)
    )
    res_proj = await db.execute(stmt_proj)
    if not res_proj.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    new_task = ProjectTask(
        project_id=project_id,
        title=request.title,
        description=request.description,
        column=request.column,
        status=request.status,
        employee_id=request.employee_id,
        due_date=request.due_date,
        company_id=cast("UUID", getattr(current_user, "company_id", None)),
    )
    db.add(new_task)
    await db.commit()

    # Reload with relation
    stmt_reload = (
        select(ProjectTask)
        .where(ProjectTask.id == new_task.id)
        .options(selectinload(ProjectTask.assignee), selectinload(ProjectTask.project))
    )
    res_reload = await db.execute(stmt_reload)
    return res_reload.scalar_one()


@router.get("/{project_id}/tasks", response_model=list[ProjectTaskSchema])
async def list_project_tasks(
    project_id: UUID,
    db: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.projects, PermissionAction.read))],
) -> list[ProjectTask]:
    stmt = (
        select(ProjectTask)
        .where(
            ProjectTask.project_id == project_id,
            ProjectTask.company_id == getattr(current_user, "company_id", None),
        )
        .options(selectinload(ProjectTask.assignee), selectinload(ProjectTask.project))
    )
    res = await db.execute(stmt)
    return list(res.scalars().all())


@router.get("/tasks/all", response_model=list[ProjectTaskSchema])
async def list_all_company_tasks(
    db: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.projects, PermissionAction.read))],
) -> list[ProjectTask]:
    stmt = (
        select(ProjectTask)
        .where(ProjectTask.company_id == getattr(current_user, "company_id", None))
        .options(selectinload(ProjectTask.assignee), selectinload(ProjectTask.project))
    )
    res = await db.execute(stmt)
    return list(res.scalars().all())


@router.get("/tasks/me", response_model=list[ProjectTaskSchema])
async def list_my_tasks(
    db: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.projects, PermissionAction.read))],
) -> list[ProjectTask]:
    # Need to identify employee associated with current_user
    from app.models.enterprise.employee import Employee

    emp_stmt = select(Employee.id).where(Employee.email == getattr(current_user, "email", None))
    emp_res = await db.execute(emp_stmt)
    emp_id = emp_res.scalar_one_or_none()
    if not emp_id:
        return []

    stmt = (
        select(ProjectTask)
        .where(
            ProjectTask.employee_id == emp_id,
            ProjectTask.company_id == getattr(current_user, "company_id", None),
        )
        .options(selectinload(ProjectTask.assignee), selectinload(ProjectTask.project))
    )
    res = await db.execute(stmt)
    return list(res.scalars().all())


@router.put("/tasks/{task_id}", response_model=ProjectTaskSchema)
async def update_project_task(
    task_id: UUID,
    request: ProjectTaskUpdate,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.projects, PermissionAction.update))
    ],
) -> ProjectTask:
    stmt = select(ProjectTask).where(
        ProjectTask.id == task_id, ProjectTask.company_id == getattr(current_user, "company_id", None)
    )
    res = await db.execute(stmt)
    task = res.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    update_data = request.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(task, key, value)

    if task.status == "Done" or task.column == "Done":
        task.status = "Done"

    await db.commit()

    # Reload with relation
    stmt_reload = (
        select(ProjectTask)
        .where(ProjectTask.id == task.id)
        .options(selectinload(ProjectTask.assignee), selectinload(ProjectTask.project))
    )
    res_reload = await db.execute(stmt_reload)
    return res_reload.scalar_one()


@router.patch("/tasks/{task_id}/move", response_model=ProjectTaskSchema)
async def move_task(
    task_id: UUID,
    column: str,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.projects, PermissionAction.update))
    ],
) -> ProjectTask:
    stmt = select(ProjectTask).where(
        ProjectTask.id == task_id, ProjectTask.company_id == getattr(current_user, "company_id", None)
    )
    res = await db.execute(stmt)
    task = res.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    task.column = column
    if column == "Done":
        task.status = "Done"

    await db.commit()

    # Reload with relation
    stmt_reload = (
        select(ProjectTask)
        .where(ProjectTask.id == task.id)
        .options(selectinload(ProjectTask.assignee), selectinload(ProjectTask.project))
    )
    res_reload = await db.execute(stmt_reload)
    return res_reload.scalar_one()
