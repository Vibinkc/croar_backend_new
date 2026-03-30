import uuid
from typing import Optional, List
from sqlalchemy import String, Integer, TIMESTAMP, func, ForeignKey, Text, Boolean, Numeric, Date
from sqlalchemy.dialects.postgresql import UUID, ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text
from . import EnterpriseBase

class SimulationScenario(EnterpriseBase):
    __tablename__ = "simulation_scenarios"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()"))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(100), default="General") # CONFLICT, SALES, LEADERSHIP, EXIT_INTERVIEW
    
    # Character Persona
    character_name: Mapped[str] = mapped_column(String(100), nullable=False)
    character_role: Mapped[str] = mapped_column(String(100), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    initial_message: Mapped[str] = mapped_column(Text, nullable=False)
    
    # Meta
    difficulty: Mapped[str] = mapped_column(String(50), default="Intermediate") # Beginner, Intermediate, Advanced
    company_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now())

    company = relationship("Company")

class SimulationAssignment(EnterpriseBase):
    __tablename__ = "simulation_assignments"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()"))
    scenario_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("simulation_scenarios.id", ondelete="CASCADE"), nullable=False)
    employee_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    
    status: Mapped[str] = mapped_column(String(50), default="PENDING") # PENDING, IN_PROGRESS, COMPLETED
    due_date: Mapped[Optional[Date]] = mapped_column(Date, nullable=True)
    
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    completed_at: Mapped[Optional[TIMESTAMP]] = mapped_column(TIMESTAMP, nullable=True)

    scenario = relationship("SimulationScenario")
    employee = relationship("Employee")

class SimulationSession(EnterpriseBase):
    __tablename__ = "simulation_sessions"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()"))
    
    # Polymorphic User Support: Can be an employee OR a hiring agent (admin/recruiter)
    employee_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=True)
    hiring_agent_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=True), ForeignKey("hiring_agents.id", ondelete="CASCADE"), nullable=True)
    
    scenario_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("simulation_scenarios.id", ondelete="CASCADE"), nullable=False)
    assignment_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=True), ForeignKey("simulation_assignments.id", ondelete="SET NULL"), nullable=True)
    
    status: Mapped[str] = mapped_column(String(50), default="ONGOING") # ONGOING, COMPLETED
    
    # Store chat history as JSONB: [{"role": "user/assistant/system", "content": "..."}]
    conversation: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    
    # AI Evaluation Results
    report: Mapped[dict] = mapped_column(JSONB, nullable=True, server_default=text("'{}'::jsonb"))
    overall_score: Mapped[Optional[float]] = mapped_column(Numeric(3, 1), nullable=True)
    feedback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    completed_at: Mapped[Optional[TIMESTAMP]] = mapped_column(TIMESTAMP, nullable=True)

    employee = relationship("Employee")
    hiring_agent = relationship("HiringAgent")
    scenario = relationship("SimulationScenario")
    assignment = relationship("SimulationAssignment")
