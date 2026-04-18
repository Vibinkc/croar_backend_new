from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.enterprise.survey import SurveyInstanceStatus, SurveyInviteStatus, SurveyQuestionType


# Employee Info for Portal
class EmployeePortalInfo(BaseModel):
    id: UUID
    first_name: str
    last_name: str | None = None
    email: str

    class Config:
        from_attributes = True


# Type
class SurveyTypeBase(BaseModel):
    name: str
    description: str | None = None


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
    options: str | None = None


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
    description: str | None = None
    is_active: bool = True


class SurveyTemplateCreate(SurveyTemplateBase):
    questions: list[SurveyQuestionCreate]


class SurveyTemplate(SurveyTemplateBase):
    id: UUID
    company_id: UUID
    created_at: datetime
    updated_at: datetime
    questions: list[SurveyQuestion]
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
    employee_ids: list[UUID] | None = None  # for CUSTOM target_group


class SurveyInstance(SurveyInstanceBase):
    id: UUID
    company_id: UUID
    created_at: datetime
    template: SurveyTemplate | None = None

    class Config:
        from_attributes = True


# Invite
class SurveyInviteBase(BaseModel):
    instance_id: UUID
    employee_id: UUID
    token: str
    status: SurveyInviteStatus = SurveyInviteStatus.PENDING
    completed_at: datetime | None = None


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
    answer_value: int | None = None
    answer_text: str | None = None


class SurveySubmission(BaseModel):
    responses: list[SurveyResponseSubmit]


# Reporting
class QuestionSummary(BaseModel):
    question_id: UUID
    question_text: str
    question_type: SurveyQuestionType
    average_score: float | None = None
    response_count: int
    text_responses: list[str] = []
    distribution: dict[str, object] = {}  # For MCQ or Rating distribution


class SurveyAIAnalysis(BaseModel):
    summary: str
    performance_score: float  # 0-100
    strengths: list[str]
    weaknesses: list[str]
    recommendations: list[str]


class SurveyAIGenerateRequest(BaseModel):
    survey_type_id: UUID
    industry_nature: str
    count: int = 5


class SurveyAIGeneratedQuestion(BaseModel):
    text: str
    type: SurveyQuestionType
    options: list[str] | None = None


class SurveyReport(BaseModel):
    instance_id: UUID
    instance_name: str
    total_invites: int
    completed_invites: int
    questions: list[QuestionSummary]
    ai_analysis: SurveyAIAnalysis | None = None
