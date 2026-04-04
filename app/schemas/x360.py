from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime, date
from typing import Optional, List
from app.models.enterprise.x360 import QuestionType, QuestionCategory, RelationType, CycleStatus, AssignmentStatus

# Question
class X360QuestionBase(BaseModel):
    text: str
    type: QuestionType = QuestionType.RATING
    category: QuestionCategory = QuestionCategory.PERFORMANCE
    active_flag: bool = True

class X360QuestionCreate(X360QuestionBase):
    pass

class X360QuestionUpdate(BaseModel):
    text: Optional[str] = None
    type: Optional[QuestionType] = None
    category: Optional[QuestionCategory] = None
    active_flag: Optional[bool] = None

class X360Question(X360QuestionBase):
    id: UUID
    company_id: UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

# AI Generation
class X360AIGenerateRequest(BaseModel):
    categories: List[str]
    count: int = 5
    additional_context: Optional[str] = None
    custom_category: Optional[str] = None

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
    description: Optional[str] = None

class X360AssessmentTemplateCreate(X360AssessmentTemplateBase):
    question_ids: List[UUID]

class X360AssessmentTemplate(X360AssessmentTemplateBase):
    id: UUID
    company_id: UUID
    questions: List[X360TemplateQuestion]
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
    template_id: Optional[UUID] = None

class X360AssessmentCycleCreate(X360AssessmentCycleBase):
    ratee_ids: List[UUID]

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
    designation: Optional[str] = None

    class Config:
        from_attributes = True

class X360AssessmentAssignment(BaseModel):
    id: UUID
    cycle_id: UUID
    ratee_id: UUID
    rater_id: UUID
    relation: RelationType
    status: AssignmentStatus
    completed_at: Optional[datetime]
    created_at: datetime
    
    ratee: EmployeeInfo
    rater: EmployeeInfo

    class Config:
        from_attributes = True

# Response & Submission
class X360ResponseSubmit(BaseModel):
    question_id: UUID
    answer_value: Optional[int] = None
    answer_text: Optional[str] = None

class X360AssessmentSubmit(BaseModel):
    responses: List[X360ResponseSubmit]

# Report
class CategoryScore(BaseModel):
    category: QuestionCategory
    self_score: Optional[float] = None
    manager_score: Optional[float] = None
    peer_score: Optional[float] = None
    report_score: Optional[float] = None
    overall_average: Optional[float] = None

class X360AIEvaluation(BaseModel):
    score: float
    summary: str

class X360Report(BaseModel):
    employee_id: UUID
    cycle_id: UUID
    template_name: str
    category_scores: List[CategoryScore]
    text_responses: List[dict] # {category, question, relation, answer}
    ai_evaluation: Optional[X360AIEvaluation] = None
    total_assignments: int
    completed_assignments: int
