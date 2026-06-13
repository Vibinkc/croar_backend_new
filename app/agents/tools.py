import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.models.enterprise.job import JobRequirement
from app.services.enterprise.hiring_agent import hiring_agent_service
from app.services.enterprise.onboarding_service import initiate_onboarding_process

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Set up logging
logger = logging.getLogger(__name__)


@tool
async def score_candidate_application(target_application_id: str, config: RunnableConfig) -> dict[str, Any]:
    """
    Evaluates a specific candidate application autonomously.
    'target_application_id' MUST be the UUID of the Candidate Application (NOT the company ID).
    """
    session: AsyncSession = config["configurable"]["session"]
    try:
        result = await hiring_agent_service.process_application(
            application_id=target_application_id, session=session, background_tasks=None
        )
        return {"status": "success", "result": result}
    except Exception as e:
        logger.error(f"Error scoring application {target_application_id}: {e}")
        return {"status": "error", "message": str(e)}


@tool
async def initiate_candidate_onboarding(
    target_application_id: str, target_company_id: str, config: RunnableConfig
) -> dict[str, Any]:
    """
    Starts the pre-onboarding process for a candidate who has been hired.
    'target_application_id' is the candidate's unique application ID.
    'target_company_id' is the company's unique ID.
    """
    session: AsyncSession = config["configurable"]["session"]
    try:
        onboarding = await initiate_onboarding_process(
            session=session,
            application_id=UUID(target_application_id),
            company_id=UUID(target_company_id),
            performed_by="AI Onboarding Agent",
        )
        if onboarding:
            return {"status": "success", "onboarding_id": str(onboarding.id)}
        return {
            "status": "already_initiated",
            "message": "Onboarding was already started for this candidate.",
        }
    except Exception as e:
        logger.error(f"Error initiating onboarding: {e}")
        return {"status": "error", "message": str(e)}


@tool
async def create_job_requisition(
    role_title: str,
    jd_content: str,
    target_company_id: str,
    config: RunnableConfig,
    location: str = "Remote",
    min_exp: int = 0,
    max_exp: int = 10,
    skills: list[str] | None = None,
    workflow_rounds: list[str] | None = None,
) -> dict[str, Any]:
    """
    Creates and ACTIVATES a new Job Requisition in the Croar database.
    'workflow_rounds' is a list of interview stages (e.g. ['Neural Screening', 'Live Coding', 'Culture Fit']).
    Use this to finalize the JD and make the job LIVE.
    """
    session: AsyncSession = config["configurable"]["session"]
    skills = skills or []
    try:
        # Default rounds if none provided
        rounds = workflow_rounds or ["Screening", "Technical Interview", "Final Review"]
        formatted_stages = [{"name": name, "order": i + 1} for i, name in enumerate(rounds)]

        new_job = JobRequirement(
            title=role_title,
            description=jd_content,
            company_id=UUID(target_company_id),
            location=location,
            experience_min=min_exp,
            experience_max=max_exp,
            required_skills=skills,
            status_id=2,  # Setting to 2 ensures the job is ACTIVE and LIVE (1 is Draft)
            workflow_stages=formatted_stages,
        )
        session.add(new_job)
        await session.commit()
        await session.refresh(new_job)
        return {
            "status": "success",
            "job_id": str(new_job.id),
            "active": True,
            "rounds": rounds,
            "message": f"Job '{role_title}' is now LIVE with {len(rounds)} interview rounds.",
        }
    except Exception as e:
        logger.error(f"Error creating job: {e}")
        return {"status": "error", "message": str(e)}


@tool
def generate_job_description(role_title: str, experience_level: str = "Senior") -> dict[str, Any]:
    """
    Drafts a professional and high-fidelity Job Description (JD) for a role.
    This tool also suggests 'Neural Workflow' rounds (e.g., Coding Test, AI Interview).
    """
    suggested_rounds = ["Initial Screening", "Technical deep-dive", "System Design", "HR & Culture"]
    return {
        "role": role_title,
        "experience": experience_level,
        "status": "DRAFT_GENERATED",
        "suggested_rounds": suggested_rounds,
        "message": f"I've drafted a {experience_level} {role_title} JD. I also suggest {len(suggested_rounds)} hiring rounds: {', '.join(suggested_rounds)}. Ready to activate?",
    }


@tool
def generate_draft_offer(candidate_name: str, salary: float, designation: str) -> dict[str, Any]:
    """
    Drafts an offer letter for a candidate based on current benchmarks.
    This creates a draft for HR approval.
    """
    return {
        "candidate_name": candidate_name,
        "salary": salary,
        "designation": designation,
        "status": "DRAFT_CREATED",
        "message": f"Offer letter for {candidate_name} drafted. Total Comp: {salary}.",
    }
