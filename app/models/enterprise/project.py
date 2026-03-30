import uuid
from typing import Optional, List
from sqlalchemy import Column, String, Text, ForeignKey, Table, Date, TIMESTAMP, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text
from . import EnterpriseBase

# Many-to-Many Association Table
project_members = Table(
    'project_members',
    EnterpriseBase.metadata,
    Column('project_id', UUID(as_uuid=True), ForeignKey('projects.id', ondelete="CASCADE"), primary_key=True),
    Column('employee_id', UUID(as_uuid=True), ForeignKey('employees.id', ondelete="CASCADE"), primary_key=True),
    Column('role', String(100), nullable=True),
    Column('joined_at', TIMESTAMP, server_default=func.now())
)

class Project(EnterpriseBase):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="Active") # Active, Completed, On Hold
    start_date: Mapped[Optional[Date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[Date]] = mapped_column(Date, nullable=True)
    
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    kanban_columns: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[\"Planning\", \"Development\", \"Testing\", \"Done\"]'::jsonb"))
    
    # Relationships
    company = relationship("Company", backref="projects")
    members = relationship("Employee", secondary=project_members, backref="projects")
    tasks = relationship("ProjectTask", back_populates="project", cascade="all, delete-orphan")

    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[Optional[TIMESTAMP]] = mapped_column(TIMESTAMP, nullable=True)

class ProjectTask(EnterpriseBase):
    __tablename__ = "project_tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()"))
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    employee_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    column: Mapped[str] = mapped_column(String(100), nullable=False) # Must match one of kanban_columns
    status: Mapped[str] = mapped_column(String(50), default="Pending") # Optional additional status
    due_date: Mapped[Optional[Date]] = mapped_column(Date, nullable=True)
    
    # Relationships
    project = relationship("Project", back_populates="tasks")
    assignee = relationship("Employee", backref="assigned_tasks")

    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now())
