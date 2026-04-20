from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ProjectBase(BaseModel):
    name: str
    description: str | None = None
    status: str = "Active"
    start_date: date | None = None
    end_date: date | None = None
    kanban_columns: list[str] = ["Planning", "Development", "Testing", "Done"]


class ProjectCreate(ProjectBase):
    company_id: UUID


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    kanban_columns: list[str] | None = None


class EmployeeSummary(BaseModel):
    id: UUID
    first_name: str
    last_name: str
    employee_id: str
    designation: str | None = None

    model_config = ConfigDict(from_attributes=True)


class ProjectTaskBase(BaseModel):
    title: str
    description: str | None = None
    column: str
    status: str = "Pending"
    due_date: date | None = None
    employee_id: UUID | None = None


class ProjectTaskCreate(ProjectTaskBase):
    pass


class ProjectTaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    column: str | None = None
    status: str | None = None
    due_date: date | None = None
    employee_id: UUID | None = None


class ProjectSummary(BaseModel):
    id: UUID
    name: str

    model_config = ConfigDict(from_attributes=True)


class ProjectTaskOut(ProjectTaskBase):
    id: UUID
    project_id: UUID
    created_at: datetime
    updated_at: datetime
    assignee: EmployeeSummary | None = None
    project: ProjectSummary | None = None

    model_config = ConfigDict(from_attributes=True)


class ProjectOut(ProjectBase):
    id: UUID
    company_id: UUID
    created_at: datetime
    updated_at: datetime
    members: list[EmployeeSummary] = []
    tasks: list[ProjectTaskOut] = []

    model_config = ConfigDict(from_attributes=True)


class ProjectMemberAdd(BaseModel):
    employee_id: UUID
    role: str | None = None
