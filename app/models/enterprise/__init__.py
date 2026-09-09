from .activity import ACTIVITY_TYPES, Activity, activity_assignees
from .assessment import AssessmentAttempt, AssessmentAutomation, AssessmentTemplate
from .base import EnterpriseBase
from .candidate import ApplicationStatus, Candidate, CandidateApplication
from .communication import EmailLog, EmailTemplate, MailAutomation
from .company import Company
from .customization import (
    CUSTOM_FIELD_ENTITIES,
    CUSTOM_FIELD_TYPES,
    TAG_ENTITIES,
    CustomFieldDefinition,
    CustomFieldValue,
    Tag,
    tag_assignments,
)
from .employee import Department, Employee
from .folder import CandidateFolder, CandidateFolderMember
from .guest import GUEST_ACCESS_LEVELS, GUEST_STATUSES, Guest, guest_jobs
from .hiring_agent import HiringAgent
from .interview import Interview, InterviewAttempt, InterviewAutomation, InterviewSchedule
from .job import JobPosting, JobRequirement, JobStatus
from .onboarding import (
    Onboarding,
    OnboardingActivity,
    OnboardingAutomation,
    OnboardingDocument,
    OnboardingNote,
    OnboardingStatus,
    OnboardingTask,
    OnboardingTemplate,
)
from .project import Project, project_members
from .simulation import SimulationScenario, SimulationSession
from .student import Student
from .survey import SurveyInstance, SurveyInvite, SurveyQuestion, SurveyResponse, SurveyTemplate, SurveyType
from .user_group import UserGroup, user_group_members, user_group_roles
from .user_role import EnterpriseUser
from .x360 import (
    X360AssessmentAssignment,
    X360AssessmentCycle,
    X360AssessmentResponse,
    X360AssessmentTemplate,
    X360EmployeeRaterMap,
    X360Question,
    X360TemplateQuestion,
)

__all__ = [
    "ACTIVITY_TYPES",
    "CUSTOM_FIELD_ENTITIES",
    "CUSTOM_FIELD_TYPES",
    "GUEST_ACCESS_LEVELS",
    "GUEST_STATUSES",
    "TAG_ENTITIES",
    "Activity",
    "ApplicationStatus",
    "AssessmentAttempt",
    "AssessmentAutomation",
    "AssessmentTemplate",
    "Candidate",
    "CandidateApplication",
    "CandidateFolder",
    "CandidateFolderMember",
    "Company",
    "CustomFieldDefinition",
    "CustomFieldValue",
    "Department",
    "EmailLog",
    "EmailTemplate",
    "Employee",
    "EnterpriseBase",
    "EnterpriseUser",
    "Guest",
    "HiringAgent",
    "Interview",
    "InterviewAttempt",
    "InterviewAutomation",
    "InterviewSchedule",
    "JobPosting",
    "JobRequirement",
    "JobStatus",
    "MailAutomation",
    "Onboarding",
    "OnboardingActivity",
    "OnboardingAutomation",
    "OnboardingDocument",
    "OnboardingNote",
    "OnboardingStatus",
    "OnboardingTask",
    "OnboardingTemplate",
    "Project",
    "SimulationScenario",
    "SimulationSession",
    "Student",
    "SurveyInstance",
    "SurveyInvite",
    "SurveyQuestion",
    "SurveyResponse",
    "SurveyTemplate",
    "SurveyType",
    "Tag",
    "UserGroup",
    "X360AssessmentAssignment",
    "X360AssessmentCycle",
    "X360AssessmentResponse",
    "X360AssessmentTemplate",
    "X360EmployeeRaterMap",
    "X360Question",
    "X360TemplateQuestion",
    "activity_assignees",
    "guest_jobs",
    "project_members",
    "tag_assignments",
    "user_group_members",
    "user_group_roles",
]

# Register payroll models so SQLAlchemy can resolve Employee.salary_structures
# (relationship target "SalaryStructure") whenever the enterprise models load —
# needed by standalone scripts (master_setup.py, db_diag.py) that configure
# mappers without importing the API routers.
import app.models.payroll  # noqa: F401
