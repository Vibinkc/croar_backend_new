import re
import json
import logging
import asyncio
from typing import Optional, Dict, Any, List
from uuid import UUID
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enterprise.communication import MailAutomation, EmailTemplate, EmailLog
from app.models.enterprise.assessment import AssessmentAutomation, AssessmentTemplate, AssessmentType
from app.models.enterprise.candidate import Candidate, CandidateApplication
from app.models.enterprise.job import JobRequirement
from app.models.enterprise.company import Company
from app.models.enterprise.user_role import EnterpriseUser as HiringAgent
from app.core.ai import analyze_text_with_llm
from app.core.settings import settings as _settings
from app.tasks.email_tasks import send_scheduled_email_task

logger = logging.getLogger(__name__)

async def evaluate_criteria(criteria: str, context: Dict[str, Any]) -> bool:
    """
    Evaluates a free-text criteria string against the provided context.
    Supports simple numeric comparisons like 'score > 80' via regex,
    and falls back to LLM for complex criteria.
    """
    criteria_clean = criteria.lower().strip() if criteria else ""
    
    if not criteria_clean or criteria_clean in ("always", "true", "none", "n/a", ""):
        return True
    
    # 1. Simple numeric comparison regex (e.g., 'score > 80', 'ai score >= 90')
    # Matches: 'score', 'ai score', 'match score' followed by '>', '<', '>=', '<=', '=', '==' and a number
    num_pattern = r"(?:ai\s+)?(?:match\s+)?score\s*([><=]{1,2})\s*(\d+(?:\.\d+)?)"
    match = re.search(num_pattern, criteria_clean)
    
    if match:
        op = match.group(1)
        threshold = float(match.group(2))
        
        # Determine which score to use based on the criteria text
        if "assessment" in criteria_clean or "test" in criteria_clean:
            actual_score = context.get("assessment_score")
        else:
            actual_score = context.get("ai_match_score")
        
        if actual_score is None:
            # Fallback to whatever score is available if only one exists in context
            actual_score = context.get("assessment_score") or context.get("ai_match_score")
            
        if actual_score is None:
            return False
            
        try:
            if op == ">": return actual_score > threshold
            if op == ">=": return actual_score >= threshold
            if op == "<": return actual_score < threshold
            if op == "<=": return actual_score <= threshold
            if op in ("=", "=="): return actual_score == threshold
        except Exception as e:
            logger.error(f"Error evaluating numeric criteria: {e}")
            
    # 2. Fallback to LLM for complex criteria
    if not _settings.openai_api_key:
        logger.warning("No OpenAI key for complex criteria evaluation. Skipping.")
        return False
        
    prompt = f"""
    Evaluate if a candidate application meets the following criteria.
    
    CRITERIA: {criteria}
    
    CANDIDATE CONTEXT:
    {json.dumps(context, indent=2, default=str)}
    
    Respond with ONLY 'TRUE' or 'FALSE'.
    """
    
    try:
        response = await analyze_text_with_llm(prompt)
        return "TRUE" in response.upper()
    except Exception as e:
        logger.error(f"LLM criteria evaluation failed: {e}")
        return False

async def trigger_automations(
    application_id: UUID,
    stage_index: int,
    session: AsyncSession,
    background_tasks: Any = None
):
    """
    Fetches all enabled automations for the job/stage, evaluates them, and sends emails.
    """
    # 1. Fetch Application + Relations
    app_stmt = select(CandidateApplication).where(CandidateApplication.id == application_id)
    res = await session.execute(app_stmt)
    application = res.scalar_one_or_none()
    
    if not application:
        logger.warning(f"Automation triggered for non-existent application {application_id}")
        return

    job_stmt = select(JobRequirement).where(JobRequirement.id == application.job_requirement_id)
    res = await session.execute(job_stmt)
    job = res.scalar_one_or_none()
    
    cand_stmt = select(Candidate).where(Candidate.id == application.candidate_id)
    res = await session.execute(cand_stmt)
    candidate = res.scalar_one_or_none()
    
    if not (job and candidate):
        return

    # 2. Fetch Automations
    auto_stmt = select(MailAutomation).where(
        MailAutomation.job_requirement_id == job.id,
        MailAutomation.stage_index == stage_index,
        MailAutomation.is_enabled == True
    )
    res = await session.execute(auto_stmt)
    automations = res.scalars().all()
    
    # 3. Build Context for Evaluation
    context = {
        "ai_match_score": float(application.ai_match_score) if application.ai_match_score is not None else None,
        "current_stage": application.current_stage,
        "full_name": candidate.full_name,
        "email": candidate.email,
        "skills": candidate.skills,
        "job_title": job.title,
        "applied_at": application.applied_at
    }

    # 4. Evaluate & Trigger Mail Automations
    for auto in automations:
        passed = await evaluate_criteria(auto.criteria, context)
        logger.info(f"Automation {auto.id} evaluation: {'PASSED' if passed else 'FAILED'} | Criteria: '{auto.criteria}' | Score: {context.get('ai_match_score')}")
        if passed:
            # Handle Scheduling
            is_immediate = getattr(auto, "is_immediate", True)
            send_at = getattr(auto, "send_at", None)
            
            logger.info(f"Automation {auto.id} scheduling: is_immediate={is_immediate}, send_at={send_at}")

            if is_immediate:
                # Send Email Immediately
                logger.info(f"Sending immediate email via automation {auto.id}")
                await send_automated_email(auto, application, candidate, job, session, background_tasks)
            else:
                # Log as Scheduled
                # Ensure send_at is naive UTC for DB comparison
                if send_at and send_at.tzinfo:
                    send_at = send_at.astimezone(timezone.utc).replace(tzinfo=None)
                
                effective_send_at = send_at or datetime.utcnow()
                logger.info(f"Scheduling automated email for {candidate.email} at {effective_send_at} via Celery task")
                
                # Ensure send_at is aware UTC for Celery ETA if naive
                eta = effective_send_at
                if eta.tzinfo is None:
                    eta = eta.replace(tzinfo=timezone.utc)

                send_scheduled_email_task.apply_async(
                    args=[str(auto.id), str(application.id), str(candidate.id), str(job.id)],
                    eta=eta
                )
                
                # Optional: Still log to DB as 'scheduled' for UI visibility
                await schedule_automated_email(auto, application, candidate, job, session, effective_send_at)
            
            # 4.1 Handle Auto-Move to Next Round (ONLY if immediate)
            if is_immediate and getattr(auto, "auto_move", False):
                # Safety: Only move if there is a next stage in the workflow
                # Stage indices are 1-based, so if current_stage < total_stages, we can move.
                max_stage = len(job.workflow_stages) if job.workflow_stages else 5
                if application.current_stage < max_stage:
                    logger.info(f"Auto-moving application {application.id} to next stage {application.current_stage + 1} (Immediate)")
                    application.current_stage += 1
                    await session.flush()
                    await session.commit() # Ensure the move is saved
                    # Trigger automations for the new stage recursively
                    await trigger_automations(application_id, application.current_stage, session, background_tasks)
                else:
                    logger.warning(f"Auto-move skipped for application {application.id}: already at last stage {max_stage}")

    # 5. Handle Assessment Automations
    as_auto_stmt = select(AssessmentAutomation).where(
        AssessmentAutomation.job_requirement_id == job.id,
        AssessmentAutomation.stage_index == stage_index,
        AssessmentAutomation.is_enabled == True
    )
    res = await session.execute(as_auto_stmt)
    as_automations = res.scalars().all()

    for auto in as_automations:
        passed = await evaluate_criteria(auto.criteria, context)
        logger.info(f"Assessment Automation {auto.id} evaluation: {'PASSED' if passed else 'FAILED'} | Criteria: '{auto.criteria}'")
        if passed:
            is_immediate = getattr(auto, "is_immediate", True)
            send_at = getattr(auto, "send_at", None)
            
            if is_immediate:
                await send_assessment_invitation(auto, application, candidate, job, session, background_tasks)
            else:
                # Log as Scheduled
                if send_at and send_at.tzinfo:
                    send_at = send_at.astimezone(timezone.utc).replace(tzinfo=None)
                
                effective_send_at = send_at or datetime.utcnow()
                logger.info(f"Scheduling assessment invitation for {candidate.email} at {effective_send_at} via Celery task")
                
                eta = effective_send_at
                if eta.tzinfo is None:
                    eta = eta.replace(tzinfo=timezone.utc)

                from app.tasks.assessment_tasks import send_scheduled_assessment_task
                send_scheduled_assessment_task.apply_async(
                    args=[str(auto.id), str(application.id), str(candidate.id)],
                    eta=eta
                )

    # 6. Handle Interview Automations
    from app.models.enterprise.interview import InterviewAutomation
    from app.services.enterprise.interview_service import schedule_candidate_interview
    
    int_auto_stmt = select(InterviewAutomation).where(
        InterviewAutomation.job_requirement_id == job.id,
        InterviewAutomation.stage_index == stage_index,
        InterviewAutomation.is_enabled == True
    )
    res = await session.execute(int_auto_stmt)
    int_automations = res.scalars().all()
    
    for auto in int_automations:
        passed = await evaluate_criteria(auto.criteria, context)
        logger.info(f"Interview Automation {auto.id} evaluation: {'PASSED' if passed else 'FAILED'} | Criteria: '{auto.criteria}'")
        if passed:
            logger.info(f"Scheduling interview for {candidate.email} via automation {auto.id}")
            await schedule_candidate_interview(session, application, auto)

    # 7. Handle Onboarding Automations
    from app.models.enterprise.onboarding import OnboardingAutomation
    from app.services.enterprise.onboarding_service import initiate_onboarding_process
    
    onb_auto_stmt = select(OnboardingAutomation).where(
        OnboardingAutomation.job_requirement_id == job.id,
        OnboardingAutomation.stage_index == stage_index,
        OnboardingAutomation.is_enabled == True
    )
    res = await session.execute(onb_auto_stmt)
    onb_automations = res.scalars().all()
    
    for auto in onb_automations:
        passed = await evaluate_criteria("", context) # Always pass for now or add criteria if model updated
        if passed:
            logger.info(f"Triggering onboarding for {candidate.email} via automation {auto.id}")
            await initiate_onboarding_process(
                session=session,
                application_id=application.id,
                template_id=auto.template_id,
                email_template_id=auto.email_template_id,
                performed_by="System Automation",
                background_tasks=background_tasks
            )

            # Auto-Move logic removed to prevent accidental moves to 'Rejected' column.
            # The candidate should remain in the stage where the automation triggered,
            # or be moved explicitly by the UI/Workflow logic.




async def send_assessment_invitation(
    automation: AssessmentAutomation,
    application: CandidateApplication,
    candidate: Candidate,
    job: JobRequirement,
    session: AsyncSession,
    background_tasks: Any = None
):
    """
    Triggers an internal AI-generated assessment invitation for an automation.
    """
    await _send_assessment_email_logic(
        source_id=automation.id,
        topic=automation.topic,
        test_type=automation.type,
        test_duration=automation.test_duration,
        email_template_id=automation.email_template_id,
        candidate=candidate,
        job=job,
        application=application,
        session=session,
        background_tasks=background_tasks
    )

async def send_manual_assessment_invitation(
    template: AssessmentTemplate,
    application: CandidateApplication,
    candidate: Candidate,
    job: JobRequirement,
    session: AsyncSession,
    background_tasks: Any = None
):
    """
    Triggers an internal AI-generated assessment invitation for a manual template-based send.
    """
    await _send_assessment_email_logic(
        source_id=template.id,
        topic=template.topic,
        test_type=template.type,
        test_duration=template.test_duration,
        email_template_id=template.email_template_id,
        candidate=candidate,
        job=job,
        application=application,
        session=session,
        background_tasks=background_tasks
    )

async def _send_assessment_email_logic(
    source_id: UUID,
    topic: str,
    test_type: str,
    test_duration: int,
    email_template_id: Optional[UUID],
    candidate: Candidate,
    job: JobRequirement,
    application: CandidateApplication,
    session: AsyncSession,
    background_tasks: Any = None
):
    """
    Core logic for sending assessment invitation emails.
    """
    logger.info(f"Triggering Internal assessment for {candidate.email}")
    
    # Internal test link
    # In production, use the actual frontend domain
    test_link = f"{_settings.frontend_url}/assessment/take/{source_id}"
    
    # Clean up test type string (remove Enum class name if present)
    clean_type = str(test_type).split(".")[-1].replace("_", " ").title()
    
    # Default format (Plain text but structured)
    subject = f"Assessment Invitation: {topic.title()} for {job.title}"
    body = f"""Hello {candidate.full_name},

As part of your application for the {job.title} position, we invite you to take a technical assessment.

--------------------------------------------------
ASSESSMENT DETAILS:
Topic:    {topic.title()}
Type:     {clean_type}
Duration: {test_duration} minutes
--------------------------------------------------

You can start the test by clicking the link below:
{test_link}

Note: Use your registered email ({candidate.email}) to verify and start the test.

Best regards,
The {job.title} Hiring Team
"""

    if email_template_id:
        tpl_stmt = select(EmailTemplate).where(EmailTemplate.id == email_template_id)
        res = await session.execute(tpl_stmt)
        template = res.scalar_one_or_none()
        if template:
            # Get Company/Agent metadata for placeholders
            comp_stmt = select(Company).limit(1)
            res = await session.execute(comp_stmt)
            company = res.scalar_one_or_none()
            company_name = company.name if company else "Our Company"
            company_address = company.location if company and company.location else ""

            agent_stmt = select(HiringAgent).limit(1)
            res = await session.execute(agent_stmt)
            agent = res.scalar_one_or_none()
            recruiter_name = f"{agent.first_name} {agent.last_name or ''}".strip() if agent else "Recruiting Team"

            replacements = {
                "candidate_name": candidate.full_name or "Candidate",
                "candidate_email": candidate.email,
                "job_title": job.title,
                "job_name": job.title,
                "company_name": company_name,
                "recruiter_name": recruiter_name,
                "company_address": company_address,
                "test_link": test_link,
                "aptitude_link": test_link,
                "coding_link": test_link,
                "assessment_link": test_link,
                "onboarding_url": "", # Placeholder if needed
                "onboarding_link": "",
                "test_duration": str(test_duration),
                "test_topic": topic,
                "test_type": clean_type,
                "current_year": str(datetime.now().year)
            }
            
            subject = template.subject or subject
            body = template.body or body
            
            for key, val in replacements.items():
                # Replace both {{key}} and {{ key }}
                for placeholder in [f"{{{{{key}}}}}", f"{{{{ {key} }}}}"]:
                    subject = subject.replace(placeholder, str(val))
                    body = body.replace(placeholder, str(val))
            
            # Advanced: Detect [URL]Label and convert to Button
            # Pattern: [http://...]Label or [{{placeholder}}]Label
            button_pattern = r"\[(https?://[^\s\]]+)\]([^\n\r<]+)"
            def make_button(m):
                url = m.group(1)
                label = m.group(2).strip()
                return f'<center><a href="{url}" style="display:inline-block;padding:14px 30px;background-color:#6e8efb;color:#ffffff;text-decoration:none;border-radius:8px;font-weight:bold;margin:20px 0;">{label}</a></center>'
            
            body = re.sub(button_pattern, make_button, str(body))
            
            # Convert newlines to HTML and wrap in celebratory template
            body_html = body.replace("\n", "<br>")
            from app.core.email_templates import wrap_in_celebratory_template
            body = wrap_in_celebratory_template(body_html, title="Assessment Invitation!")
            
            # Safety: If test link is not in the body, already handled by celebratory template (optional)
            # but we'll remove the old append logic as it's messy for HTML
    
    # Logic to send email
    try:
        from app.router.enterprise.communication import send_smtp_email
        from fastapi.concurrency import run_in_threadpool
        # Register the log
        log = EmailLog(
            candidate_id=candidate.id,
            application_id=application.id,
            recipient_email=candidate.email,
            sender_email=_settings.mailer_sender_email,
            subject=subject,
            body=body,
            status="SENT",
            direction="outbound",
            sent_at=datetime.utcnow()
        )
        session.add(log)
        await session.flush()
        
        if background_tasks:
            background_tasks.add_task(send_smtp_email, candidate.email, subject, body)
        else:
            await run_in_threadpool(send_smtp_email, candidate.email, subject, body)
            
    except Exception as e:
        logger.error(f"Failed to send assessment email: {e}")

async def send_automated_email(
    automation: MailAutomation,
    application: CandidateApplication,
    candidate: Candidate,
    job: JobRequirement,
    session: AsyncSession,
    background_tasks: Any = None
):
    """
    Actually sends the email based on the automation's template.
    Refactored from communication.py logic.
    """
    # 1. Get Template
    tpl_stmt = select(EmailTemplate).where(EmailTemplate.id == automation.template_id)
    res = await session.execute(tpl_stmt)
    template = res.scalar_one_or_none()
    
    if not template:
        logger.error(f"Template {automation.template_id} not found for automation {automation.id}")
        return

    # 2. Get Company/Agent metadata for placeholders
    comp_stmt = select(Company).limit(1)
    res = await session.execute(comp_stmt)
    company = res.scalar_one_or_none()
    
    # Since we are system-triggered, we'll use a generic "Recruiting Team" or a specific agent if possible
    # For now, let's use the first agent found or a default
    agent_stmt = select(HiringAgent).limit(1)
    res = await session.execute(agent_stmt)
    agent = res.scalar_one_or_none()
    
    recruiter_name = f"{agent.first_name} {agent.last_name or ''}".strip() if agent else "Recruiting Team"
    
    # 3. Replace Placeholders
    variables = {
        "candidate_name": candidate.full_name or "Candidate",
        "job_title": job.title,
        "company_name": company.name if company else "Our Company",
        "recruiter_name": recruiter_name,
        "application_id": str(application.id),
        "current_year": str(datetime.now().year)
    }
    
    subject = template.subject
    body = template.body
    
    for key, val in variables.items():
        # Replace both {{key}} and {{ key }}
        for placeholder in [f"{{{{{key}}}}}", f"{{{{ {key} }}}}"]:
            subject = subject.replace(placeholder, str(val))
            body = body.replace(placeholder, str(val))

    # 4. Send Implementation (Direct SMTP call)
    from app.router.enterprise.communication import send_smtp_email
    
    def do_send():
        success, err = send_smtp_email(candidate.email, subject, body)
        
        # 5. Log the sending (Create a new session because background tasks might need it)
        # Note: In a real app we'd use a separate session or handle commit carefully
        # For simplicity, we'll assume background_tasks is handled by FastAPI
        pass

    if background_tasks:
        background_tasks.add_task(do_send)
    else:
        # Fallback to sync or run_in_threadpool
        import asyncio
        from fastapi.concurrency import run_in_threadpool
        asyncio.create_task(run_in_threadpool(do_send))

    # Log to DB immediately
    log = EmailLog(
        candidate_id=candidate.id,
        application_id=application.id,
        template_id=template.id,
        sender_email=_settings.mailer_sender_email,
        recipient_email=candidate.email,
        subject=subject,
        body=body,
        direction="outbound",
        sent_at=datetime.utcnow(),
        status="sent" # Optimistic
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
    send_at: datetime
):
    """
    Logs an email to be sent later by the background worker.
    """
    # 1. Get Template
    tpl_stmt = select(EmailTemplate).where(EmailTemplate.id == automation.template_id)
    res = await session.execute(tpl_stmt)
    template = res.scalar_one_or_none()
    
    if not template:
        logger.error(f"Template {automation.template_id} not found for automation {automation.id}")
        return

    # 2. Get Company/Agent metadata for placeholders
    comp_stmt = select(Company).limit(1)
    res = await session.execute(comp_stmt)
    company = res.scalar_one_or_none()
    
    agent_stmt = select(HiringAgent).limit(1)
    res = await session.execute(agent_stmt)
    agent = res.scalar_one_or_none()
    recruiter_name = f"{agent.first_name} {agent.last_name or ''}".strip() if agent else "Recruiting Team"
    
    # 3. Replace Placeholders (Prepare the final body/subject)
    variables = {
        "candidate_name": candidate.full_name or "Candidate",
        "job_title": job.title,
        "company_name": company.name if company else "Our Company",
        "recruiter_name": recruiter_name,
        "application_id": str(application.id),
        "current_year": str(datetime.now().year)
    }
    
    subject = template.subject
    body = template.body
    
    for key, val in variables.items():
        # Replace both {{key}} and {{ key }}
        for placeholder in [f"{{{{{key}}}}}", f"{{{{ {key} }}}}"]:
            subject = subject.replace(placeholder, str(val))
            body = body.replace(placeholder, str(val))

    # 4. Log to DB with 'scheduled' status
    log = EmailLog(
        candidate_id=candidate.id,
        application_id=application.id,
        automation_id=automation.id,
        template_id=template.id,
        sender_email=_settings.mailer_sender_email,
        recipient_email=candidate.email,
        subject=subject,
        body=body,
        direction="outbound",
        sent_at=send_at,
        status="scheduled"
    )
    session.add(log)
    await session.flush()
    # No commit here, let the caller handle it or flush it

async def process_scheduled_emails():
    """
    DEPRECATED: Background worker was replaced by Celery.
    """
    logger.info("Custom background worker is deprecated. Use Celery instead.")
    return


# Worker start was moved to main.py lifespan

