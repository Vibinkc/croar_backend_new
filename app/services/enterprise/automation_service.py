import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.settings import get_settings
from app.models.enterprise.assessment import AssessmentAutomation
from app.models.enterprise.candidate import Candidate, CandidateApplication
from app.models.enterprise.communication import EmailLog, EmailTemplate, MailAutomation
from app.models.enterprise.company import Company
from app.models.enterprise.job import JobRequirement
from app.models.enterprise.user_role import EnterpriseUser as HiringAgent

logger = logging.getLogger(__name__)
_settings = get_settings()
background_tasks_set = set()


async def evaluate_criteria(criteria: str, context: dict[str, Any]) -> bool:
    """
    Evaluates a free-text criteria against a given context using basic logic or LLM.
    Example: "score > 80", "experience > 5".
    """
    await asyncio.sleep(0)  # async kept for awaiting callers / future async (LLM) evaluation
    if not criteria or criteria.strip() == "":
        return True

    # Simple implementation for common patterns
    try:
        crit_lower = criteria.lower()
        if "ai_score" in crit_lower:
            ai_score = float(context.get("ai_score", 0))
            if ">" in criteria:
                val = float(criteria.split(">")[1].strip())
                return ai_score > val
            if "<" in criteria:
                val = float(criteria.split("<")[1].strip())
                return ai_score < val
            # A numeric gate with no parseable operator must not silently fire.
            return False
    except Exception as e:
        logger.error(f"Criteria evaluation error: {e}")
        # A malformed numeric gate fails CLOSED (don't fire the action).
        if "ai_score" in criteria.lower():
            return False

    # Fallback: Treat as a prompt for a small logical evaluation (could use LLM here)
    return True


async def trigger_automations(
    application_id: UUID,
    stage_index: int,
    session: AsyncSession,
    background_tasks: BackgroundTasks | None = None,
) -> None:
    """
    Checks and executes all automations (Email, Assessment, Interview)
    triggered by a candidate reaching a specific hiring stage.
    """
    logger.info(f"Triggering automations for application {application_id} at stage {stage_index}")

    # 1. Fetch full context
    stmt = (
        select(CandidateApplication)
        .options(
            selectinload(CandidateApplication.candidate), selectinload(CandidateApplication.job_requirement)
        )
        .where(CandidateApplication.id == application_id)
    )
    res = await session.execute(stmt)
    application = res.scalar_one_or_none()

    if not application:
        logger.error(f"Application {application_id} not found for automation trigger.")
        return

    candidate = application.candidate
    job = application.job_requirement

    # Orphaned application (candidate/job deleted): skip rather than 500 after the
    # stage change may already be committed.
    if not candidate or not job:
        logger.error(f"Application {application_id} is missing candidate/job; skipping automations.")
        return

    context: dict[str, Any] = {
        "candidate_name": candidate.full_name,
        "job_title": job.title,
        "current_stage": stage_index,
        "ai_score": float(application.ai_match_score) if application.ai_match_score else 0,
    }

    # 2. Handle Mail Automations
    mail_stmt = select(MailAutomation).where(
        MailAutomation.job_requirement_id == job.id,
        MailAutomation.stage_index == stage_index,
        MailAutomation.is_enabled,
    )
    mail_res = await session.execute(mail_stmt)
    mail_automations = list(mail_res.scalars().all())

    for mail_auto in mail_automations:
        passed = await evaluate_criteria(mail_auto.criteria, context)
        logger.info(
            f"Mail Automation {mail_auto.id} evaluation: "
            f"{'PASSED' if passed else 'FAILED'} | Criteria: '{mail_auto.criteria}'"
        )
        if passed:
            if mail_auto.is_immediate:
                await send_automated_email(mail_auto, application, candidate, job, session, background_tasks)
            else:
                # Schedule via Celery
                from app.tasks.email_tasks import send_scheduled_email_task

                send_at = mail_auto.send_at or datetime.now(UTC)
                if send_at.tzinfo is None:
                    send_at = send_at.replace(tzinfo=UTC)

                logger.info(
                    f"Scheduling email for {candidate.email} at {send_at} via Celery task {mail_auto.id}"
                )
                send_scheduled_email_task.apply_async(
                    args=[str(mail_auto.id), str(application.id), str(candidate.id), str(job.id)], eta=send_at
                )

                # Log as 'scheduled'
                await schedule_automated_email(
                    mail_auto, application, candidate, job, session, send_at.replace(tzinfo=None)
                )

            # 3. Handle Auto-Move (if enabled)
            if mail_auto.auto_move and mail_auto.is_immediate:
                # Check if there are more stages
                workflow = cast("list[dict[str, Any]]", job.workflow_stages) or []
                max_stage = len(workflow)
                if application.current_stage < max_stage:
                    logger.info(
                        f"Auto-moving application {application.id} to stage {application.current_stage + 1}"
                    )
                    application.current_stage += 1
                    await session.flush()
                    # Recurse for the next stage
                    await trigger_automations(
                        application_id, application.current_stage, session, background_tasks
                    )
                else:
                    logger.warning(
                        f"Auto-move skipped for application {application.id}: "
                        f"already at last stage {max_stage}"
                    )

    # 4. Handle Assessment Automations
    as_auto_stmt = select(AssessmentAutomation).where(
        AssessmentAutomation.job_requirement_id == job.id,
        AssessmentAutomation.stage_index == stage_index,
        AssessmentAutomation.is_enabled,
    )
    as_res = await session.execute(as_auto_stmt)
    as_automations = list(as_res.scalars().all())

    for ass_auto in as_automations:
        passed = await evaluate_criteria(ass_auto.criteria, context)
        logger.info(
            f"Assessment Automation {ass_auto.id} evaluation: "
            f"{'PASSED' if passed else 'FAILED'} | Criteria: '{ass_auto.criteria}'"
        )
        if passed:
            if ass_auto.is_immediate:
                await send_assessment_invitation(
                    ass_auto, application, candidate, job, session, background_tasks
                )
            else:
                # Log as Scheduled
                send_at_val = ass_auto.send_at or datetime.now(UTC)
                if send_at_val.tzinfo is None:
                    send_at_val = send_at_val.replace(tzinfo=UTC)

                logger.info(
                    f"Scheduling assessment invitation for {candidate.email} at {send_at_val} via Celery task"
                )

                from app.tasks.assessment_tasks import send_scheduled_assessment_task

                send_scheduled_assessment_task.apply_async(
                    args=[str(ass_auto.id), str(application.id), str(candidate.id)], eta=send_at_val
                )

    # 5. Handle Interview Automations
    from app.models.enterprise.interview import InterviewAutomation
    from app.services.enterprise.interview_service import schedule_candidate_interview

    int_auto_stmt = select(InterviewAutomation).where(
        InterviewAutomation.job_requirement_id == job.id,
        InterviewAutomation.stage_index == stage_index,
        InterviewAutomation.is_enabled,
    )
    int_res = await session.execute(int_auto_stmt)
    int_automations = list(int_res.scalars().all())

    for interview_auto in int_automations:
        passed = await evaluate_criteria(interview_auto.criteria, context)
        logger.info(
            f"Interview Automation {interview_auto.id} evaluation: "
            f"{'PASSED' if passed else 'FAILED'} | Criteria: '{interview_auto.criteria}'"
        )
        if passed:
            logger.info(f"Scheduling interview for {candidate.email} via automation {interview_auto.id}")
            await schedule_candidate_interview(session, application, interview_auto)

    # 6. Handle Onboarding Automations
    from app.models.enterprise.onboarding import OnboardingAutomation
    from app.services.enterprise.onboarding_service import initiate_onboarding_process

    onb_auto_stmt = select(OnboardingAutomation).where(
        OnboardingAutomation.job_requirement_id == job.id,
        OnboardingAutomation.stage_index == stage_index,
        OnboardingAutomation.is_enabled,
    )
    onb_res = await session.execute(onb_auto_stmt)
    onb_automations = list(onb_res.scalars().all())

    for onboarding_auto in onb_automations:
        passed = await evaluate_criteria("", context)  # Always pass for now or add criteria if model updated
        if passed:
            logger.info(f"Triggering onboarding for {candidate.email} via automation {onboarding_auto.id}")
            await initiate_onboarding_process(
                session=session,
                application_id=application.id,
                company_id=cast("UUID", job.company_id),
                template_id=onboarding_auto.template_id,
                email_template_id=onboarding_auto.email_template_id,
                performed_by="System Automation",
                background_tasks=background_tasks,
            )


async def send_assessment_invitation(
    automation: AssessmentAutomation,
    application: CandidateApplication,
    candidate: Candidate,
    job: JobRequirement,
    session: AsyncSession,
    background_tasks: BackgroundTasks | None = None,
) -> None:
    """
    Sends an assessment invitation email and logs the attempt.
    """
    logger.info(f"Sending assessment invitation for {candidate.email}")

    # 1. Fetch Template
    tpl_id = automation.email_template_id
    if not tpl_id:
        # Fallback to a default or skip
        logger.warning(f"No email template assigned to Assessment Automation {automation.id}")
        return

    tpl_stmt = select(EmailTemplate).where(EmailTemplate.id == tpl_id)
    tpl_res = await session.execute(tpl_stmt)
    template = tpl_res.scalar_one_or_none()

    if not template:
        logger.error(f"Template {tpl_id} not found for assessment automation.")
        return

    # 2. Get Company metadata
    comp_stmt = select(Company).limit(1)
    comp_res = await session.execute(comp_stmt)
    company = comp_res.scalar_one_or_none()

    # 3. Replace Placeholders
    # Assessment-specific: add magic link
    assessment_link = f"{_settings.frontend_url}/public/assessment/{application.id}/start"

    variables: dict[str, str] = {
        "candidate_name": candidate.full_name or "Candidate",
        "job_title": job.title,
        "company_name": company.name if company else "Our Company",
        "assessment_link": assessment_link,
        "test_duration": str(automation.test_duration),
        "topic": automation.topic,
    }

    subject = template.subject
    body = template.body

    for key, val in variables.items():
        for placeholder in [f"{{{{{key}}}}}", f"{{{{ {key} }}}}"]:
            subject = subject.replace(placeholder, str(val))
            body = body.replace(placeholder, str(val))

    # 4. Send via Background Task
    from app.router.enterprise.communication import send_smtp_email

    def do_send() -> None:
        send_smtp_email(str(candidate.email), subject, body)

    if background_tasks:
        background_tasks.add_task(do_send)
    else:
        import asyncio

        from fastapi.concurrency import run_in_threadpool

        task = asyncio.create_task(run_in_threadpool(do_send))
        background_tasks_set.add(task)
        task.add_done_callback(background_tasks_set.discard)

    # 5. Log as 'sent'
    log = EmailLog(
        candidate_id=candidate.id,
        application_id=application.id,
        template_id=template.id,
        sender_email=_settings.mailer_sender_email,
        recipient_email=str(candidate.email),
        subject=subject,
        body=body,
        direction="outbound",
        status="sent",
    )
    session.add(log)
    await session.flush()


async def send_automated_email(
    automation: MailAutomation,
    application: CandidateApplication,
    candidate: Candidate,
    job: JobRequirement,
    session: AsyncSession,
    background_tasks: BackgroundTasks | None = None,
) -> EmailLog | None:
    """
    Core implementation to send an immediate automated email.
    """
    # 1. Get Template
    tpl_stmt = select(EmailTemplate).where(EmailTemplate.id == automation.template_id)
    tpl_res = await session.execute(tpl_stmt)
    template = tpl_res.scalar_one_or_none()

    if not template:
        logger.error(f"Template {automation.template_id} not found for automation {automation.id}")
        return None

    # 2. Get Company/Agent metadata
    comp_stmt = select(Company).limit(1)
    comp_res = await session.execute(comp_stmt)
    company = comp_res.scalar_one_or_none()

    agent_stmt = select(HiringAgent).limit(1)
    agent_res = await session.execute(agent_stmt)
    agent = agent_res.scalar_one_or_none()
    recruiter_name = f"{agent.first_name} {agent.last_name or ''}".strip() if agent else "Recruiting Team"

    # 3. Replace Placeholders
    variables: dict[str, str] = {
        "candidate_name": candidate.full_name or "Candidate",
        "job_title": job.title,
        "company_name": company.name if company else "Our Company",
        "recruiter_name": recruiter_name,
        "application_id": str(application.id),
        "current_year": str(datetime.now().year),
    }

    subject = template.subject
    body = template.body

    for key, val in variables.items():
        for placeholder in [f"{{{{{key}}}}}", f"{{{{ {key} }}}}"]:
            subject = subject.replace(placeholder, str(val))
            body = body.replace(placeholder, str(val))

    # 4. Send via Background Task
    from app.router.enterprise.communication import send_smtp_email

    def do_send() -> None:
        send_smtp_email(str(candidate.email), subject, body)

    if background_tasks:
        background_tasks.add_task(do_send)
    else:
        import asyncio

        from fastapi.concurrency import run_in_threadpool

        task = asyncio.create_task(run_in_threadpool(do_send))
        background_tasks_set.add(task)
        task.add_done_callback(background_tasks_set.discard)

    # 5. Log
    log = EmailLog(
        candidate_id=candidate.id,
        application_id=application.id,
        template_id=template.id,
        sender_email=_settings.mailer_sender_email,
        recipient_email=str(candidate.email),
        subject=subject,
        body=body,
        direction="outbound",
        status="sent",
    )
    session.add(log)
    await session.flush()
    return log


async def schedule_automated_email(
    automation: MailAutomation,
    application: CandidateApplication,
    candidate: Candidate,
    job: JobRequirement,
    session: AsyncSession,
    send_at: datetime,
) -> None:
    """
    Logs an email to be sent later by the background worker.
    """
    # 1. Get Template
    tpl_stmt = select(EmailTemplate).where(EmailTemplate.id == automation.template_id)
    tpl_res = await session.execute(tpl_stmt)
    template = tpl_res.scalar_one_or_none()

    if not template:
        logger.error(f"Template {automation.template_id} not found for automation {automation.id}")
        return

    # 2. Get Company/Agent metadata
    comp_stmt = select(Company).limit(1)
    comp_res = await session.execute(comp_stmt)
    company = comp_res.scalar_one_or_none()

    agent_stmt = select(HiringAgent).limit(1)
    agent_res = await session.execute(agent_stmt)
    agent = agent_res.scalar_one_or_none()
    recruiter_name = f"{agent.first_name} {agent.last_name or ''}".strip() if agent else "Recruiting Team"

    # 3. Replace Placeholders
    variables: dict[str, str] = {
        "candidate_name": candidate.full_name or "Candidate",
        "job_title": job.title,
        "company_name": company.name if company else "Our Company",
        "recruiter_name": recruiter_name,
        "application_id": str(application.id),
        "current_year": str(datetime.now().year),
    }

    subject = template.subject
    body = template.body

    for key, val in variables.items():
        for placeholder in [f"{{{{{key}}}}}", f"{{{{ {key} }}}}"]:
            subject = subject.replace(placeholder, str(val))
            body = body.replace(placeholder, str(val))

    # 4. Log as 'scheduled'
    log = EmailLog(
        candidate_id=candidate.id,
        application_id=application.id,
        automation_id=automation.id,
        template_id=template.id,
        sender_email=_settings.mailer_sender_email,
        recipient_email=str(candidate.email),
        subject=subject,
        body=body,
        direction="outbound",
        sent_at=send_at,
        status="scheduled",
    )
    session.add(log)
    await session.flush()
