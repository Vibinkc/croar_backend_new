from pydantic import BaseModel, ConfigDict
from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import datetime, date

class ProjectBase(BaseModel):
    name: str
    description: Optional[str] = None
    status: str = "Active"
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    kanban_columns: List[str] = ["Planning", "Development", "Testing", "Done"]

class ProjectCreate(ProjectBase):
    company_id: UUID

class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    kanban_columns: Optional[List[str]] = None

class EmployeeSummary(BaseModel):
    id: UUID
    first_name: str
    last_name: str
    employee_id: str
    designation: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class ProjectTaskBase(BaseModel):
    title: str
    description: Optional[str] = None
    column: str
    status: str = "Pending"
    due_date: Optional[date] = None
    employee_id: Optional[UUID] = None

class ProjectTaskCreate(ProjectTaskBase):
    pass

class ProjectTaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    column: Optional[str] = None
    status: Optional[str] = None
    due_date: Optional[date] = None
    employee_id: Optional[UUID] = None

class ProjectTaskOut(ProjectTaskBase):
    id: UUID
    project_id: UUID
    created_at: datetime
    updated_at: datetime
    assignee: Optional[EmployeeSummary] = None

    model_config = ConfigDict(from_attributes=True)

class ProjectOut(ProjectBase):
    id: UUID
    company_id: UUID
    created_at: datetime
    updated_at: datetime
    members: List[EmployeeSummary] = []
    tasks: List[ProjectTaskOut] = []

    model_config = ConfigDict(from_attributes=True)

class ProjectMemberAdd(BaseModel):
    employee_id: UUID
    role: Optional[str] = None
