from pydantic import BaseModel, ConfigDict
from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import datetime, date

class SimulationScenarioBase(BaseModel):
    title: str
    description: Optional[str] = None
    category: str = "General"
    character_name: str
    character_role: str
    system_prompt: str
    initial_message: str
    difficulty: str = "Intermediate"

class SimulationScenarioCreate(SimulationScenarioBase):
    pass

class SimulationScenarioUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    character_name: Optional[str] = None
    character_role: Optional[str] = None
    system_prompt: Optional[str] = None
    initial_message: Optional[str] = None
    difficulty: Optional[str] = None

class SimulationScenarioSchema(SimulationScenarioBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    company_id: UUID
    created_at: datetime
    updated_at: datetime

class SimulationAssignmentCreate(BaseModel):
    scenario_id: UUID
    employee_ids: List[UUID]
    due_date: Optional[date] = None

class SimulationAssignmentSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    scenario_id: UUID
    employee_id: UUID
    status: str
    due_date: Optional[date] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    scenario: Optional[SimulationScenarioSchema] = None

class SimulationSessionBase(BaseModel):
    scenario_id: UUID
    assignment_id: Optional[UUID] = None

class SimulationSessionCreate(SimulationSessionBase):
    employee_id: Optional[UUID] = None

class SimulationSessionSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    employee_id: Optional[UUID] = None
    hiring_agent_id: Optional[UUID] = None
    scenario_id: UUID
    status: str
    conversation: List[Dict[str, str]]
    report: Optional[Dict[str, Any]] = None
    overall_score: Optional[float] = None
    feedback: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    scenario: Optional[SimulationScenarioSchema] = None

class SimulationChatMessage(BaseModel):
    message: str

class SimulationChatResponse(BaseModel):
    reply: str
    status: str
    feedback_hint: Optional[str] = None

class AIGenerateScenarioRequest(BaseModel):
    prompt: str

class SimulationResultSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    employee_name: str
    scenario_title: str
    category: str
    status: str
    overall_score: Optional[float] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
