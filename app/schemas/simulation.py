from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SimulationScenarioBase(BaseModel):
    title: str
    description: str | None = None
    category: str = "General"
    character_name: str
    character_role: str
    system_prompt: str
    initial_message: str
    difficulty: str = "Intermediate"


class SimulationScenarioCreate(SimulationScenarioBase):
    pass


class SimulationScenarioUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    category: str | None = None
    character_name: str | None = None
    character_role: str | None = None
    system_prompt: str | None = None
    initial_message: str | None = None
    difficulty: str | None = None


class SimulationScenarioSchema(SimulationScenarioBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    company_id: UUID
    created_at: datetime
    updated_at: datetime


class SimulationAssignmentCreate(BaseModel):
    scenario_id: UUID
    employee_ids: list[UUID]
    due_date: date | None = None


class SimulationAssignmentSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    scenario_id: UUID
    employee_id: UUID
    status: str
    due_date: date | None = None
    created_at: datetime
    completed_at: datetime | None = None
    scenario: SimulationScenarioSchema | None = None


class SimulationSessionBase(BaseModel):
    scenario_id: UUID
    assignment_id: UUID | None = None


class SimulationSessionCreate(SimulationSessionBase):
    employee_id: UUID | None = None


class SimulationSessionSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    employee_id: UUID | None = None
    hiring_agent_id: UUID | None = None
    scenario_id: UUID
    status: str
    conversation: list[dict[str, str]]
    report: dict[str, Any] | None = None
    overall_score: float | None = None
    feedback: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
    scenario: SimulationScenarioSchema | None = None


class SimulationChatMessage(BaseModel):
    message: str


class SimulationChatResponse(BaseModel):
    reply: str
    status: str
    feedback_hint: str | None = None


class AIGenerateScenarioRequest(BaseModel):
    prompt: str


class SimulationResultSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    employee_name: str
    scenario_title: str
    category: str
    status: str
    overall_score: float | None = None
    created_at: datetime
    completed_at: datetime | None = None
