from app.core.database import Base

class EnterpriseBase(Base):
    __abstract__ = True

from .company import Company
from .user_role import EnterpriseUser, Role
from .job import JobRequirement, JobPosting, JobStatus
from .candidate import Candidate, CandidateApplication, ApplicationStatus
from .interview import Interview, InterviewSchedule, InterviewAttempt, InterviewAutomation
from .communication import EmailTemplate, EmailLog, MailAutomation
from .hiring_agent import HiringAgent
from .student import Student
from .assessment import AssessmentAutomation, AssessmentAttempt, AssessmentTemplate
from .onboarding import Onboarding, OnboardingStatus, OnboardingDocument, OnboardingActivity, OnboardingTask, OnboardingNote, OnboardingAutomation
from .employee import Employee, Department
from .project import Project, project_members
from .x360 import (
    X360Question, 
    X360AssessmentTemplate, 
    X360TemplateQuestion, 
    X360AssessmentCycle, 
    X360AssessmentAssignment, 
    X360AssessmentResponse,
    X360EmployeeRaterMap
)
from .survey import (
    SurveyType,
    SurveyTemplate,
    SurveyQuestion,
    SurveyInstance,
    SurveyInvite,
    SurveyResponse
)
from .simulation import SimulationScenario, SimulationSession

__all__ = [
    "EnterpriseBase", "Company", "EnterpriseUser", "Role",
    "JobRequirement", "JobPosting", "JobStatus",
    "Candidate", "CandidateApplication", "ApplicationStatus",
    "Interview", "InterviewSchedule", "InterviewAttempt", "InterviewAutomation",
    "EmailTemplate", "EmailLog", "MailAutomation", "HiringAgent",
    "Student", "AssessmentAutomation", "AssessmentAttempt", "AssessmentTemplate",
    "Onboarding", "OnboardingStatus", "OnboardingDocument", "OnboardingActivity", "OnboardingTask", "OnboardingNote", "OnboardingAutomation",
    "Employee", "Department", "Project", "project_members",
    "X360Question", "X360AssessmentTemplate", "X360TemplateQuestion",
    "X360AssessmentCycle", "X360AssessmentAssignment", "X360AssessmentResponse",
    "X360EmployeeRaterMap",
    "SurveyType", "SurveyTemplate", "SurveyQuestion",
    "SurveyInstance", "SurveyInvite", "SurveyResponse",
    "SimulationScenario", "SimulationSession"
]
