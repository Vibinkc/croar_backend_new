from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime, date
from typing import Optional, List
from app.models.enterprise.survey import SurveyQuestionType, SurveyInstanceStatus, SurveyInviteStatus

# Employee Info for Portal
class EmployeePortalInfo(BaseModel):
    id: UUID
    first_name: str
    last_name: Optional[str] = None
    email: str
    class Config:
        from_attributes = True

# Type
class SurveyTypeBase(BaseModel):
    name: str
    description: Optional[str] = None

class SurveyType(SurveyTypeBase):
    id: UUID
    class Config:
        from_attributes = True

# Question
class SurveyQuestionBase(BaseModel):
    text: str
    type: SurveyQuestionType = SurveyQuestionType.RATING
    order: int = 0
    scale_min: int = 1
    scale_max: int = 5
    options: Optional[str] = None

class SurveyQuestionCreate(SurveyQuestionBase):
    pass

class SurveyQuestion(SurveyQuestionBase):
    id: UUID
    class Config:
        from_attributes = True

# Template
class SurveyTemplateBase(BaseModel):
    survey_type_id: UUID
    title: str
    description: Optional[str] = None
    is_active: bool = True

class SurveyTemplateCreate(SurveyTemplateBase):
    questions: List[SurveyQuestionCreate]

class SurveyTemplate(SurveyTemplateBase):
    id: UUID
    company_id: UUID
    created_at: datetime
    updated_at: datetime
    questions: List[SurveyQuestion]
    survey_type: SurveyType
    
    class Config:
        from_attributes = True

# Instance
class SurveyInstanceBase(BaseModel):
    template_id: UUID
    name: str
    start_date: date
    end_date: date
    status: SurveyInstanceStatus = SurveyInstanceStatus.DRAFT
    target_group: str = "ALL"

class SurveyInstanceCreate(SurveyInstanceBase):
    employee_ids: Optional[List[UUID]] = None # for CUSTOM target_group

class SurveyInstance(SurveyInstanceBase):
    id: UUID
    company_id: UUID
    created_at: datetime
    template: Optional[SurveyTemplate] = None
    
    class Config:
        from_attributes = True

# Invite
class SurveyInviteBase(BaseModel):
    instance_id: UUID
    employee_id: UUID
    token: str
    status: SurveyInviteStatus = SurveyInviteStatus.PENDING
    completed_at: Optional[datetime] = None

class SurveyInvite(SurveyInviteBase):
    id: UUID
    class Config:
        from_attributes = True

class SurveyInviteFull(SurveyInvite):
    instance: SurveyInstance
    employee: EmployeePortalInfo
    class Config:
        from_attributes = True

# Response
class SurveyResponseSubmit(BaseModel):
    question_id: UUID
    answer_value: Optional[int] = None
    answer_text: Optional[str] = None

class SurveySubmission(BaseModel):
    responses: List[SurveyResponseSubmit]

# Reporting
class QuestionSummary(BaseModel):
    question_id: UUID
    question_text: str
    question_type: SurveyQuestionType
    average_score: Optional[float] = None
    response_count: int
    text_responses: List[str] = []
    distribution: dict = {} # For MCQ or Rating distribution

class SurveyAIAnalysis(BaseModel):
    summary: str
    performance_score: float # 0-100
    strengths: List[str]
    weaknesses: List[str]
    recommendations: List[str]

class SurveyAIGenerateRequest(BaseModel):
    survey_type_id: UUID
    industry_nature: str
    count: int = 5

class SurveyAIGeneratedQuestion(BaseModel):
    text: str
    type: SurveyQuestionType
    options: Optional[List[str]] = None

class SurveyReport(BaseModel):
    instance_id: UUID
    instance_name: str
    total_invites: int
    completed_invites: int
    questions: List[QuestionSummary]
    ai_analysis: Optional[SurveyAIAnalysis] = None
