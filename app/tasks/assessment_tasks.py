import logging
from uuid import UUID

from asgiref.sync import async_to_sync
from sqlalchemy import select

from app.core.celery_app import celery_app
from app.core.database import db_manager
from app.models.enterprise.assessment import AssessmentAutomation
from app.models.enterprise.candidate import Candidate, CandidateApplication
from app.models.enterprise.job import JobRequirement
from app.services.enterprise.automation_service import send_assessment_invitation

logger = logging.getLogger(__name__)


@celery_app.task(name="send_scheduled_assessment_task")  # type: ignore
def send_scheduled_assessment_task(
    automation_id_str: str, application_id_str: str, candidate_id_str: str
) -> None:
    """
    Celery task to send a scheduled assessment invitation.
    """
    automation_id = UUID(automation_id_str)
    application_id = UUID(application_id_str)
    candidate_id = UUID(candidate_id_str)

    logger.info(f"Executing scheduled assessment invitation for application {application_id}")

    # We use a synchronous wrapper for the async service call
    @async_to_sync
    async def run_send() -> None:
        async with db_manager.session() as session:
            # Fetch necessary records
            # 1. Automation
            auto_stmt = select(AssessmentAutomation).where(AssessmentAutomation.id == automation_id)
            auto_res = await session.execute(auto_stmt)
            automation = auto_res.scalar_one_or_none()

            if not automation or not automation.is_enabled:
                logger.warning(f"Automation {automation_id} not found or disabled. Skipping.")
                return

            # 2. Application
            app_stmt = select(CandidateApplication).where(CandidateApplication.id == application_id)
            app_res = await session.execute(app_stmt)
            application = app_res.scalar_one_or_none()

            if not application:
                logger.warning(f"Application {application_id} not found. Skipping.")
                return

            # 3. Candidate
            cand_stmt = select(Candidate).where(Candidate.id == candidate_id)
            cand_res = await session.execute(cand_stmt)
            candidate = cand_res.scalar_one_or_none()

            if not candidate:
                logger.warning(f"Candidate {candidate_id} not found. Skipping.")
                return

            # 4. Job
            job_stmt = select(JobRequirement).where(JobRequirement.id == application.job_requirement_id)
            job_res = await session.execute(job_stmt)
            job = job_res.scalar_one_or_none()

            if not job:
                logger.warning(f"Job not found for application {application_id}. Skipping.")
                return

            # Trigger the invitation
            await send_assessment_invitation(
                automation=automation, application=application, candidate=candidate, job=job, session=session
            )

            await session.commit()
            logger.info(f"Successfully sent scheduled assessment invitation for {candidate.email}")

    try:
        run_send()
    except Exception as e:
        logger.error(f"Failed to send scheduled assessment invitation: {e!s}", exc_info=True)
        raise e
