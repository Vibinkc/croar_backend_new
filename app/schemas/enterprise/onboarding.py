from pydantic import BaseModel, ConfigDict
from uuid import UUID
from datetime import datetime
from typing import Optional, List, Any, Dict
from app.schemas.enterprise.applications import ApplicationResponse
from app.schemas.enterprise.communication import EmailTemplateResponse

class OnboardingTemplateBase(BaseModel):
    name: str
    description: Optional[str] = None
    sections: List[str] = [] # e.g. ["job_info", "personal_info", "education_info", "documents"]
    required_documents: List[Dict[str, Any]] = [] # e.g. [{"name": "Aadhar Card", "description": "ID"}]
    form_config: Dict[str, Any] = {}

class OnboardingTemplateCreate(OnboardingTemplateBase):
    pass

class OnboardingTemplateResponse(OnboardingTemplateBase):
    id: UUID
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

class OnboardingStatusResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)

class OnboardingDocumentResponse(BaseModel):
    id: UUID
    onboarding_id: UUID
    name: str
    doc_type: Optional[str] = None
    file_path: Optional[str] = None
    status: str
    due_date: Optional[datetime] = None
    comment: Optional[str] = None
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

class OnboardingActivityResponse(BaseModel):
    id: UUID
    onboarding_id: UUID
    action: str
    performed_by: str
    timestamp: datetime
    metadata_info: Optional[dict] = None
    
    model_config = ConfigDict(from_attributes=True)

class OnboardingTaskResponse(BaseModel):
    id: UUID
    onboarding_id: UUID
    title: str
    description: Optional[str] = None
    priority: str
    status: str
    due_date: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)

class OnboardingNoteResponse(BaseModel):
    id: UUID
    onboarding_id: UUID
    content: str
    author_name: str
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

class OnboardingResponse(BaseModel):
    id: UUID
    application_id: UUID
    onboarding_code: str
    status_id: int
    status: Optional[OnboardingStatusResponse] = None
    template_id: Optional[UUID] = None
    template: Optional[OnboardingTemplateResponse] = None
    
    job_info: Optional[dict] = None
    personal_info: Optional[dict] = None
    education_info: Optional[dict] = None
    other_info: Optional[dict] = None
    form_data: Optional[dict] = None
    
    candidate_email: Optional[str] = None
    job_title: Optional[str] = None
    company_name: Optional[str] = None
    company_logo: Optional[str] = None
    
    initiation_date: datetime
    completed_at: Optional[datetime] = None
    
    application: Optional[ApplicationResponse] = None
    
    documents: List[OnboardingDocumentResponse] = []
    activities: List[OnboardingActivityResponse] = []
    tasks: List[OnboardingTaskResponse] = []
    notes: List[OnboardingNoteResponse] = []
    rejected_fields: List[str] = []
    
    model_config = ConfigDict(from_attributes=True)

class OnboardingInitiateRequest(BaseModel):
    application_id: UUID
    template_id: Optional[UUID] = None

class OnboardingUpdateRequest(BaseModel):
    status_id: Optional[int] = None
    job_info: Optional[dict] = None
    personal_info: Optional[dict] = None
    education_info: Optional[dict] = None
    other_info: Optional[dict] = None
    form_data: Optional[dict] = None

class OnboardingResubmitRequest(BaseModel):
    reason: str
    rejected_document_ids: Optional[List[UUID]] = []
    rejected_fields: Optional[List[str]] = []

class OnboardingApproveRequest(BaseModel):
    notes: Optional[str] = None

class OnboardingNoteCreate(BaseModel):
    content: str

class OnboardingTaskCreate(BaseModel):
    title: str
    description: Optional[str] = None
    priority: str = "Medium"
    due_date: Optional[datetime] = None

class OnboardingDocumentRequest(BaseModel):
    name: str
    due_date: Optional[datetime] = None

class OnboardingAutomationBase(BaseModel):
    job_requirement_id: UUID
    stage_index: int
    stage_name: Optional[str] = None
    template_id: Optional[UUID] = None
    email_template_id: Optional[UUID] = None
    is_enabled: bool = True
    auto_move: bool = False

class OnboardingAutomationCreate(OnboardingAutomationBase):
    pass

class OnboardingAutomationUpdate(BaseModel):
    stage_index: Optional[int] = None
    stage_name: Optional[str] = None
    template_id: Optional[UUID] = None
    email_template_id: Optional[UUID] = None
    is_enabled: Optional[bool] = None
    auto_move: Optional[bool] = None

class OnboardingAutomationResponse(OnboardingAutomationBase):
    id: UUID
    created_at: datetime
    updated_at: datetime
    
    template: Optional[OnboardingTemplateResponse] = None
    email_template: Optional[EmailTemplateResponse] = None

    model_config = ConfigDict(from_attributes=True)

