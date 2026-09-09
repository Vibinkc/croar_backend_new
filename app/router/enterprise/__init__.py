from fastapi import APIRouter

from .activities import router as activities_router
from .administration import router as administration_router
from .applications import router as applications_router
from .assessment import router as assessment_router
from .assessment_templates import router as assessment_templates_router
from .audio import router as audio_router
from .automation import router as automation_router
from .candidate_assessment import router as candidate_assessment_router
from .candidate_interview import router as candidate_interview_router
from .candidate_search import router as candidate_search_router
from .candidates import router as candidates_router
from .career_page import router as career_page_router
from .communication import router as communication_router
from .company import router as company_router
from .credits import router as credits_router
from .dashboard import router as dashboard_router
from .data_management import router as data_management_router
from .employees import router as employees_router
from .folders import router as folders_router
from .guests import portal as guest_portal_router
from .guests import router as guests_router
from .hiring_agent import router as hiring_agent_router
from .integrations import router as integrations_router
from .interview_automation import router as interview_automation_router
from .interview_templates import router as interview_templates_router
from .job_inbox import router as job_inbox_router
from .job_portals import router as job_portals_router
from .job_workspace import router as job_workspace_router
from .jobs import router as jobs_router
from .matches import router as matches_router
from .onboarding import router as onboarding_router
from .onboarding_automation import router as onboarding_automation_router
from .onboarding_templates import router as onboarding_templates_router
from .projects import router as projects_router
from .public import router as public_router
from .public_assessment import router as public_assessment_router
from .public_onboarding import router as public_onboarding_router
from .reports import router as reports_router
from .simulation import router as simulation_router
from .skill_assessment import router as skill_assessment_router
from .sourcing import router as sourcing_router
from .sourcing_chat import router as sourcing_chat_router
from .sourcing_hub import router as sourcing_hub_router
from .survey import router as survey_router
from .team import router as team_router
from .upload import router as upload_router
from .user_groups import router as user_groups_router
from .x360 import router as x360_router

router = APIRouter()
router.include_router(public_router)
router.include_router(public_assessment_router)
router.include_router(communication_router)
router.include_router(applications_router)
router.include_router(dashboard_router)
router.include_router(jobs_router)
router.include_router(career_page_router)
router.include_router(activities_router)
router.include_router(administration_router)
router.include_router(data_management_router)
router.include_router(guests_router)
router.include_router(guest_portal_router)
router.include_router(user_groups_router)
router.include_router(folders_router)
router.include_router(matches_router)
router.include_router(reports_router)
router.include_router(candidate_search_router)
router.include_router(sourcing_hub_router)
router.include_router(job_inbox_router)
router.include_router(job_workspace_router)
router.include_router(integrations_router)
router.include_router(job_portals_router)
router.include_router(company_router)
router.include_router(hiring_agent_router)
router.include_router(automation_router)
router.include_router(assessment_router)
router.include_router(assessment_templates_router)
router.include_router(candidate_assessment_router)
router.include_router(interview_automation_router)
router.include_router(onboarding_router)
router.include_router(public_onboarding_router)
router.include_router(onboarding_templates_router)
router.include_router(interview_templates_router)
router.include_router(candidate_interview_router)
router.include_router(onboarding_automation_router)
router.include_router(upload_router)
router.include_router(employees_router)
router.include_router(candidates_router)
router.include_router(projects_router)
router.include_router(x360_router)
router.include_router(skill_assessment_router)
router.include_router(survey_router)
router.include_router(simulation_router, prefix="/simulations", tags=["Simulations"])
router.include_router(team_router)
router.include_router(credits_router)
router.include_router(sourcing_router)
router.include_router(sourcing_chat_router)
router.include_router(audio_router)
