from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.enterprise.x360 import AssignmentStatus, CycleStatus, QuestionType, RelationType


# Question
class X360QuestionBase(BaseModel):
    text: str
    type: QuestionType = QuestionType.RATING
    category: str = "PERFORMANCE"
    active_flag: bool = True


class X360QuestionCreate(X360QuestionBase):
    pass


class X360QuestionUpdate(BaseModel):
    text: str | None = None
    type: QuestionType | None = None
    category: str | None = None
    active_flag: bool | None = None


class X360Question(X360QuestionBase):
    id: UUID
    company_id: UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# AI Generation
class X360AIGenerateRequest(BaseModel):
    categories: list[str]
    count: int = 5
    additional_context: str | None = None
    custom_category: str | None = None


class X360AIGeneratedQuestion(BaseModel):
    text: str
    type: str = "RATING"
    category: str


class X360TemplateQuestionBase(BaseModel):
    question_id: UUID
    order: int = 0


class X360TemplateQuestion(X360TemplateQuestionBase):
    question: X360Question

    class Config:
        from_attributes = True


class X360AssessmentTemplateBase(BaseModel):
    name: str
    description: str | None = None


class X360AssessmentTemplateCreate(X360AssessmentTemplateBase):
    question_ids: list[UUID]


class X360AssessmentTemplate(X360AssessmentTemplateBase):
    id: UUID
    company_id: UUID
    questions: list[X360TemplateQuestion]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Cycle
class X360AssessmentCycleBase(BaseModel):
    name: str
    start_date: date
    end_date: date
    status: CycleStatus = CycleStatus.DRAFT
    template_id: UUID | None = None


class X360AssessmentCycleCreate(X360AssessmentCycleBase):
    ratee_ids: list[UUID]


class X360AssessmentCycle(X360AssessmentCycleBase):
    id: UUID
    company_id: UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Assignment
class EmployeeInfo(BaseModel):
    id: UUID
    first_name: str
    last_name: str
    email: str
    designation: str | None = None

    class Config:
        from_attributes = True


class X360AssessmentAssignment(BaseModel):
    id: UUID
    cycle_id: UUID
    ratee_id: UUID
    rater_id: UUID
    relation: RelationType
    status: AssignmentStatus
    completed_at: datetime | None
    created_at: datetime

    ratee: EmployeeInfo
    rater: EmployeeInfo

    class Config:
        from_attributes = True


# Response & Submission
class X360ResponseSubmit(BaseModel):
    question_id: UUID
    answer_value: int | None = None
    answer_text: str | None = None


class X360AssessmentSubmit(BaseModel):
    responses: list[X360ResponseSubmit]


# Report
class CategoryScore(BaseModel):
    category: str
    self_score: float | None = None
    manager_score: float | None = None
    peer_score: float | None = None
    report_score: float | None = None
    overall_average: float | None = None


class X360AIEvaluation(BaseModel):
    score: float
    summary: str


class X360Report(BaseModel):
    employee_id: UUID
    cycle_id: UUID
    template_name: str
    category_scores: list[CategoryScore]
    text_responses: list[dict[str, object]]  # {category, question, relation, answer}
    ai_evaluation: X360AIEvaluation | None = None
    total_assignments: int
    completed_assignments: int


class X360SummaryStats(BaseModel):
    active_cycles: int
    pending_my_assignments: int
    completed_my_assignments: int
    total_participants: int
