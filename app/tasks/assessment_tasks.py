from app.core.celery_app import celery_app
from app.core.database import db_manager
from app.models.enterprise.assessment import AssessmentAutomation
from app.models.enterprise.candidate import Candidate, CandidateApplication
from app.models.enterprise.job import JobRequirement
from app.services.enterprise.automation_service import send_assessment_invitation
from asgiref.sync import async_to_sync
import logging
from uuid import UUID
from sqlalchemy import select

logger = logging.getLogger(__name__)

@celery_app.task(name="send_scheduled_assessment_task")
def send_scheduled_assessment_task(automation_id_str: str, application_id_str: str, candidate_id_str: str):
    """
    Celery task to send a scheduled assessment invitation.
    """
    automation_id = UUID(automation_id_str)
    application_id = UUID(application_id_str)
    candidate_id = UUID(candidate_id_str)
    
    logger.info(f"Executing scheduled assessment invitation for application {application_id}")
    
    # We use a synchronous wrapper for the async service call
    @async_to_sync
    async def run_send():
        async with db_manager.session() as session:
            # Fetch necessary records
            # 1. Automation
            stmt = select(AssessmentAutomation).where(AssessmentAutomation.id == automation_id)
            res = await session.execute(stmt)
            automation = res.scalar_one_or_none()
            
            if not automation or not automation.is_enabled:
                logger.warning(f"Automation {automation_id} not found or disabled. Skipping.")
                return
                
            # 2. Application
            stmt = select(CandidateApplication).where(CandidateApplication.id == application_id)
            res = await session.execute(stmt)
            application = res.scalar_one_or_none()
            
            if not application:
                logger.warning(f"Application {application_id} not found. Skipping.")
                return
                
            # 3. Candidate
            stmt = select(Candidate).where(Candidate.id == candidate_id)
            res = await session.execute(stmt)
            candidate = res.scalar_one_or_none()
            
            if not candidate:
                logger.warning(f"Candidate {candidate_id} not found. Skipping.")
                return
                
            # 4. Job
            stmt = select(JobRequirement).where(JobRequirement.id == application.job_requirement_id)
            res = await session.execute(stmt)
            job = res.scalar_one_or_none()
            
            if not job:
                logger.warning(f"Job not found for application {application_id}. Skipping.")
                return

            # Trigger the invitation
            await send_assessment_invitation(
                automation=automation,
                application=application,
                candidate=candidate,
                job=job,
                session=session
            )
            
            await session.commit()
            logger.info(f"Successfully sent scheduled assessment invitation for {candidate.email}")

    try:
        run_send()
    except Exception as e:
        logger.error(f"Failed to send scheduled assessment invitation: {str(e)}", exc_info=True)
        raise e
