from datetime import datetime
from uuid import UUID

from asgiref.sync import async_to_sync
from celery.utils.log import get_task_logger
from sqlalchemy import select

from app.core.celery_app import celery_app
from app.core.database import db_manager
from app.models.enterprise.candidate import Candidate, CandidateApplication
from app.models.enterprise.communication import EmailLog, EmailTemplate, MailAutomation
from app.models.enterprise.company import Company
from app.models.enterprise.job import JobRequirement
from app.models.enterprise.user_role import EnterpriseUser as HiringAgent

logger = get_task_logger(__name__)


@celery_app.task(name="send_scheduled_email_task")
def send_scheduled_email_task(automation_id: str, application_id: str, candidate_id: str, job_id: str):
    """
    Celery task to send a scheduled email and handle deferred auto-move.
    """
    return async_to_sync(_send_scheduled_email_async)(automation_id, application_id, candidate_id, job_id)


async def _send_scheduled_email_async(
    automation_id: str, application_id: str, candidate_id: str, job_id: str
):
    logger.info(f"Starting scheduled email task for automation {automation_id}")

    async with db_manager.session() as session:
        # 1. Fetch data
        automation = await session.get(MailAutomation, UUID(automation_id))
        application = await session.get(CandidateApplication, UUID(application_id))
        candidate = await session.get(Candidate, UUID(candidate_id))
        job = await session.get(JobRequirement, UUID(job_id))

        if not all([automation, application, candidate, job]):
            logger.error(
                f"Missing data for task: auto={bool(automation)}, app={bool(application)}, cand={bool(candidate)}, job={bool(job)}"
            )
            return False

        # 2. Prepare Email (Logic copied from automation_service.py)
        tpl_stmt = select(EmailTemplate).where(EmailTemplate.id == automation.template_id)
        res = await session.execute(tpl_stmt)
        template = res.scalar_one_or_none()

        if not template:
            logger.error(f"Template {automation.template_id} not found")
            return False

        comp_stmt = select(Company).limit(1)
        res = await session.execute(comp_stmt)
        company = res.scalar_one_or_none()

        agent_stmt = select(HiringAgent).limit(1)
        res = await session.execute(agent_stmt)
        agent = res.scalar_one_or_none()
        recruiter_name = f"{agent.first_name} {agent.last_name or ''}".strip() if agent else "Recruiting Team"

        variables = {
            "candidate_name": candidate.full_name or "Candidate",
            "job_title": job.title,
            "company_name": company.name if company else "Our Company",
            "recruiter_name": recruiter_name,
            "application_id": str(application.id),
        }

        subject = template.subject
        body = template.body
        for key, val in variables.items():
            subject = subject.replace(f"{{{{{key}}}}}", str(val))
            body = body.replace(f"{{{{{key}}}}}", str(val))

        # 3. Send via SMTP
        from app.router.enterprise.communication import send_smtp_email

        success, err = send_smtp_email(candidate.email, subject, body)

        # 4. Update Logs
        # Find existing 'scheduled' log or create a new one
        log_stmt = (
            select(EmailLog)
            .where(
                EmailLog.application_id == application.id,
                EmailLog.automation_id == automation.id,
                EmailLog.status == "scheduled",
            )
            .order_by(EmailLog.id.desc())
            .limit(1)
        )
        res = await session.execute(log_stmt)
        log = res.scalar_one_or_none()

        if not log:
            log = EmailLog(
                application_id=application.id,
                automation_id=automation.id,
                candidate_id=candidate.id,
                recipient_email=candidate.email,
                subject=subject,
                body=body,
                direction="outbound",
            )
            session.add(log)

        if success:
            log.status = "sent"
            log.sent_at = datetime.utcnow()
            logger.info(f"Email successfully sent to {candidate.email}")

            # 5. Deferred Auto-Move
            if automation.auto_move:
                logger.info(f"Performing deferred auto-move for application {application.id}")
                application.current_stage += 1
                await session.flush()
                # Trigger next round automations
                from app.services.enterprise.automation_service import trigger_automations

                await trigger_automations(application.id, application.current_stage, session)
        else:
            log.status = "failed"
            log.error_message = err
            logger.error(f"Failed to send email to {candidate.email}: {err}")

        await session.commit()
        return success
