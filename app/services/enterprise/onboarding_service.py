import secrets
import string
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from fastapi import BackgroundTasks
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.email_templates import wrap_in_celebratory_template
from app.core.settings import get_settings
from app.models.enterprise.candidate import CandidateApplication
from app.models.enterprise.onboarding import (
    Onboarding,
    OnboardingActivity,
    OnboardingDocument,
    OnboardingStatus,
    OnboardingTemplate,
)
from app.router.enterprise.communication import send_smtp_email

_settings = get_settings()


def generate_onboarding_code() -> str:
    """Generate a unique onboarding code like ONB-XXXXX."""
    suffix = "".join(secrets.choice(string.digits) for _ in range(5))
    return f"ONB-{suffix}"


async def log_onboarding_activity(
    session: AsyncSession,
    onboarding_id: UUID,
    description: str,
    performed_by: str,
    activity_type: str = "SYSTEM",
) -> None:
    activity = OnboardingActivity(
        onboarding_id=onboarding_id,
        description=description,
        performed_by=performed_by,
        activity_type=activity_type,
    )
    session.add(activity)
    await session.flush()


async def _prepare_onboarding_email(
    session: AsyncSession, onboarding: Onboarding, subject: str, body: str, title: str = "Welcome Aboard!"
) -> tuple[str, str]:
    """Helper to replace placeholders in onboarding emails."""
    candidate = onboarding.application.candidate
    frontend_url = _settings.frontend_url
    onboarding_url = f"{frontend_url}/onboarding/{onboarding.id}"

    job_title = "your position"
    company_name = "Our Company"
    company_address = "N/A"

    # Try to get company info
    if onboarding.application and onboarding.application.job_requirement_id:
        from app.models.enterprise.company import Company
        from app.models.enterprise.job import JobRequirement

        comp_stmt = (
            select(Company, JobRequirement.title)
            .join(JobRequirement, JobRequirement.company_id == Company.id)
            .where(JobRequirement.id == onboarding.application.job_requirement_id)
        )
        comp_res = await session.execute(comp_stmt)
        row = comp_res.first()
        if row:
            comp_obj, j_title = row
            company_name = comp_obj.name
            company_address = comp_obj.location or "N/A"
            job_title = j_title or "your position"

    replacements = {
        "candidate_name": candidate.full_name or candidate.email,
        "candidate_email": candidate.email,
        "job_title": job_title,
        "job_name": job_title,
        "company_name": company_name,
        "onboarding_code": onboarding.onboarding_code,
        "recruiter_name": "HR Team",
        "company_address": company_address,
        "onboarding_url": onboarding_url,
        "onboarding_link": onboarding_url,
        "current_year": str(datetime.now().year),
    }

    for key, val in replacements.items():
        for placeholder in [f"{{{{{key}}}}}", f"{{{{ {key} }}}}"]:
            subject = subject.replace(placeholder, str(val))
            body = body.replace(placeholder, str(val))

    # Convert newlines to HTML and wrap in celebratory template
    body_html = body.replace("\n", "<br>")
    body = wrap_in_celebratory_template(body_html, title=title)

    return subject, body


async def initiate_onboarding_process(
    session: AsyncSession,
    application_id: UUID,
    company_id: UUID,
    template_id: UUID | None = None,
    email_template_id: UUID | None = None,
    performed_by: str = "System",
    background_tasks: BackgroundTasks | None = None,
) -> Onboarding | None:
    """
    Core logic to initiate onboarding for a candidate.
    Returns the created Onboarding object or raises Exception.
    """
    # 1. Verify application exists and belongs to the company
    stmt = (
        select(CandidateApplication)
        .options(selectinload(CandidateApplication.candidate))
        .where(CandidateApplication.id == application_id, CandidateApplication.company_id == company_id)
    )
    result = await session.execute(stmt)
    application = result.scalar_one_or_none()

    if not application:
        raise ValueError("Candidate application not found or access denied")

    check_stmt = (
        select(Onboarding)
        .where(Onboarding.application_id == application_id, Onboarding.company_id == company_id)
        .limit(1)
    )
    res_check = await session.execute(check_stmt)
    if res_check.scalar_one_or_none():
        # Already initiated, just return or handle as needed
        return None

    # 2. Get initial status
    status_stmt = select(OnboardingStatus).where(OnboardingStatus.name == "In Progress")
    res_status = await session.execute(status_stmt)
    initial_status = res_status.scalar_one_or_none()

    if not initial_status:
        raise ValueError("Default onboarding status 'In Progress' not found.")

    # 3. Get Template if provided
    template = None
    if template_id:
        template_stmt = select(OnboardingTemplate).where(
            OnboardingTemplate.id == template_id, OnboardingTemplate.company_id == company_id
        )
        res_template = await session.execute(template_stmt)
        template = res_template.scalar_one_or_none()
        if not template:
            raise ValueError("Onboarding template not found or access denied")

    # 4. Create Onboarding
    onboarding = Onboarding(
        application_id=application_id,
        company_id=company_id,
        onboarding_code=generate_onboarding_code(),
        status_id=initial_status.id,
        template_id=template.id if template else None,
        initiation_date=cast("Any", datetime.now()),
    )
    session.add(onboarding)
    await session.flush()

    # 5. Create Documents from Template if available
    if template and template.required_documents:
        required_docs = template.required_documents
        for doc_cfg in required_docs:
            doc = OnboardingDocument(
                onboarding_id=onboarding.id,
                name=cast("str", doc_cfg.get("name", "Document")),
                status="Pending",
                company_id=company_id,
            )
            session.add(doc)

    # 6. Log initial activity
    await log_onboarding_activity(
        session,
        onboarding.id,
        f"Onboarding has been initiated for Employee {application.candidate.full_name}",
        performed_by,
    )

    # Send Email Notification
    candidate = application.candidate
    if candidate:
        email_template = None
        if email_template_id:
            from app.models.enterprise.communication import EmailTemplate

            et_stmt = select(EmailTemplate).where(EmailTemplate.id == email_template_id)
            et_res = await session.execute(et_stmt)
            email_template = et_res.scalar_one_or_none()

        frontend_url = _settings.frontend_url
        onboarding_url = f"{frontend_url}/onboarding/{onboarding.id}"

        # Default content
        subject = "Welcome! Your Onboarding Process has Started"
        body = f"""
        <html>
            <body>
                <h2>Welcome to the Team!</h2>
                <p>Hello {candidate.full_name or "Candidate"},</p>
                <p>We are excited to start your onboarding process. Please click the "
                "link below to complete your profile and upload necessary documents:</p>
                <p><a href="{onboarding_url}" style="padding: 10px 20px; "
                "background-color: #4f46e5; color: white; text-decoration: none; "
                "border-radius: 8px;">Complete Onboarding</a></p>
                <p>Or copy and paste this link: {onboarding_url}</p>
                <p>Best regards,<br>HR Team</p>
            </body>
        </html>
        """

        if email_template:
            subject = email_template.subject
            body = email_template.body

        subject, body = await _prepare_onboarding_email(
            session, onboarding, subject, body, title="Welcome Aboard!"
        )

        if background_tasks:
            background_tasks.add_task(send_smtp_email, candidate.email, subject, body)
        else:
            await run_in_threadpool(send_smtp_email, candidate.email, subject, body)

        await log_onboarding_activity(session, onboarding.id, "Onboarding link sent to candidate", "System")

    await session.flush()
    return onboarding


async def send_onboarding_resubmit_email(
    session: AsyncSession,
    onboarding: Onboarding,
    reason: str,
    background_tasks: BackgroundTasks | None = None,
) -> None:
    subject = "Action Required: Onboarding Correction Needed"
    body = f"""
    Hello {onboarding.application.candidate.full_name},

    We have reviewed your onboarding submission and found that some information or documents need correction.

    REASON: {reason}

    Please click the link below to update your information:
    {{onboarding_url}}

    Best regards,
    The HR Team
    """

    subject, body = await _prepare_onboarding_email(
        session, onboarding, subject, body, title="Correction Needed"
    )

    if background_tasks:
        background_tasks.add_task(send_smtp_email, onboarding.application.candidate.email, subject, body)
    else:
        await run_in_threadpool(send_smtp_email, onboarding.application.candidate.email, subject, body)


async def send_onboarding_welcome_email(
    session: AsyncSession, onboarding: Onboarding, background_tasks: BackgroundTasks | None = None
) -> None:
    subject = "Congratulations! You're Officially Hired!"
    body = f"""
    Hello {onboarding.application.candidate.full_name},

    We are thrilled to inform you that your onboarding has been approved!
    Welcome to the team.

    We will reach out soon with your start date and next steps.

    Best regards,
    The HR Team
    """

    subject, body = await _prepare_onboarding_email(
        session, onboarding, subject, body, title="Welcome to the Team!"
    )

    if background_tasks:
        background_tasks.add_task(send_smtp_email, onboarding.application.candidate.email, subject, body)
    else:
        await run_in_threadpool(send_smtp_email, onboarding.application.candidate.email, subject, body)
