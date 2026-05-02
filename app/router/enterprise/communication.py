import json
import re
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from openai import AsyncOpenAI
from sqlalchemy import select, update

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.core.settings import get_settings
from app.models.enterprise.candidate import Candidate, CandidateApplication
from app.models.enterprise.communication import EmailLog, EmailTemplate
from app.models.enterprise.company import Company
from app.models.enterprise.job import JobRequirement
from app.models.shared.constants import ModuleScope, PermissionAction
from app.schemas.enterprise.communication import (
    EmailDraftRequest,
    EmailLogResponse,
    EmailSendRequest,
    EmailTemplateCreate,
    EmailTemplateResponse,
    EmailTemplateUpdate,
    TemplateGenerationRequest,
)
from app.services.enterprise.hiring_agent import hiring_agent_service
from app.services.enterprise.imap_service import imap_service

_settings = get_settings()

router = APIRouter(prefix="/communication", tags=["Enterprise Communication"])


async def get_communication_context(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.communications, PermissionAction.read))
    ],
    job_id: UUID | None = None,
) -> dict[str, Any]:
    """Get sender context for templates."""
    company = None
    if job_id:
        stmt = (
            select(Company)
            .join(JobRequirement, Company.id == JobRequirement.company_id)
            .where(JobRequirement.id == job_id)
            .limit(1)
        )
        result = await session.execute(stmt)
        company = result.scalar_one_or_none()

    if not company:
        company_id = getattr(current_user, "company_id", None)
        stmt = select(Company).where(Company.id == company_id).limit(1)
        result = await session.execute(stmt)
        company = result.scalar_one_or_none()

    first_name = getattr(current_user, "first_name", "")
    last_name = getattr(current_user, "last_name", "")
    recruiter_name = (f"{first_name} {last_name}").strip() or "Recruiting Team"

    return {
        "company_name": company.name if company else None,
        "company_address": company.location if company else None,
        "recruiter_name": recruiter_name,
        "recruiter_email": getattr(current_user, "email", ""),
    }


def wrap_with_layout(body: str, company_name: str, logo_url: str | None = None) -> str:
    """Wraps email body in a standard branded HTML layout."""
    actual_logo = logo_url or _settings.default_logo_url
    logo_html = (
        f'<img src="{actual_logo}" alt="{company_name}" style="max-height: 50px; margin-bottom: 20px;">'
        if actual_logo
        else f'<h2 style="color: #4f46e5; margin-bottom: 20px;">{company_name}</h2>'
    )

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto,
                             Helvetica, Arial, sans-serif;
                line-height: 1.6; color: #334155; margin: 0; padding: 0;
            }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 40px 20px; }}
            .header {{ border-bottom: 1px solid #e2e8f0; padding-bottom: 20px; margin-bottom: 30px; }}
            .footer {{ border-top: 1px solid #e2e8f0; padding-top: 20px; margin-top: 40px;
                      font-size: 12px; color: #94a3b8; text-align: center; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                {logo_html}
            </div>
            <div class="content">
                {body}
            </div>
            <div class="footer">
                &copy; {datetime.now().year} {company_name}. All rights reserved.<br>
                This is an automated message from our recruitment portal.
            </div>
        </div>
    </body>
    </html>
    """


def send_smtp_email(
    to_email: str, subject: str, body: str, company_name: str | None = None, logo_url: str | None = None
) -> tuple[bool, str]:
    try:
        actual_company = company_name or _settings.app_name
        actual_logo = logo_url or _settings.default_logo_url
        branded_body = wrap_with_layout(body, actual_company, actual_logo)
        msg = MIMEMultipart()
        msg["From"] = str(_settings.mailer_sender_email)
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(branded_body, "html"))

        with smtplib.SMTP(str(_settings.smtp_address), int(cast("Any", _settings.smtp_port))) as server:
            server.starttls()
            if _settings.smtp_username and _settings.smtp_password:
                server.login(str(_settings.smtp_username), str(_settings.smtp_password))
            server.send_message(msg)
        return True, ""
    except Exception as e:
        return False, str(e)


@router.post("/templates", response_model=EmailTemplateResponse)
async def create_template(
    request: EmailTemplateCreate,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.communications, PermissionAction.create))
    ],
) -> EmailTemplate:
    """Create a new email template."""
    new_template = EmailTemplate(
        **request.model_dump(), company_id=cast("UUID", getattr(current_user, "company_id", None))
    )
    session.add(new_template)
    await session.commit()
    await session.refresh(new_template)
    return new_template


@router.patch("/templates/{template_id}", response_model=EmailTemplateResponse)
async def update_template(
    template_id: UUID,
    request: EmailTemplateUpdate,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.communications, PermissionAction.update))
    ],
) -> EmailTemplate:
    """Update an existing email template."""
    stmt = select(EmailTemplate).where(
        EmailTemplate.id == template_id, EmailTemplate.company_id == getattr(current_user, "company_id", None)
    )
    result = await session.execute(stmt)
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    update_data = request.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(template, key, value)

    await session.commit()
    await session.refresh(template)
    return template


@router.delete("/templates/{template_id}")
async def delete_template(
    template_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.communications, PermissionAction.delete))
    ],
) -> dict[str, str]:
    """Delete an email template."""
    stmt = select(EmailTemplate).where(
        EmailTemplate.id == template_id, EmailTemplate.company_id == getattr(current_user, "company_id", None)
    )
    result = await session.execute(stmt)
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    await session.delete(template)
    await session.commit()
    return {"message": "Template deleted successfully"}


@router.get("/templates", response_model=list[EmailTemplateResponse])
async def list_templates(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.communications, PermissionAction.read))
    ],
) -> list[EmailTemplate]:
    """List all email templates for the organization."""
    stmt = (
        select(EmailTemplate)
        .where(EmailTemplate.company_id == getattr(current_user, "company_id", None))
        .order_by(EmailTemplate.created_at.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/logs", response_model=list[EmailLogResponse])
async def get_email_logs(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.communications, PermissionAction.read))
    ],
    direction: str | None = None,
) -> list[EmailLog]:
    """Get history of emails."""
    stmt = select(EmailLog).where(EmailLog.company_id == getattr(current_user, "company_id", None))
    if direction:
        stmt = stmt.where(EmailLog.direction == direction)

    stmt = stmt.order_by(EmailLog.sent_at.desc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.post("/sync-imap")
async def sync_emails_manually(
    session: DBSessionDep,
    _current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.communications, PermissionAction.moderate))
    ],
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Trigger manual IMAP sync."""
    result = await imap_service.fetch_and_sync_emails(session, background_tasks)
    return result


@router.patch("/read/{log_id}")
async def mark_as_read(
    log_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.communications, PermissionAction.update))
    ],
) -> dict[str, str]:
    """Mark an inbound email as read."""
    stmt = (
        update(EmailLog)
        .where(EmailLog.id == log_id, EmailLog.company_id == getattr(current_user, "company_id", None))
        .values(is_read=True)
    )
    await session.execute(stmt)
    await session.commit()
    return {"status": "success"}


@router.get("/smart-reply/{log_id}")
async def get_smart_reply(
    log_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.communications, PermissionAction.generate))
    ],
) -> dict[str, Any]:
    """Generate an AI smart reply for an inbound message."""
    stmt = select(EmailLog).where(
        EmailLog.id == log_id, EmailLog.company_id == getattr(current_user, "company_id", None)
    )
    res = await session.execute(stmt)
    log = res.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="Email not found")

    candidate_name = "Candidate"
    job_title = "the position"

    if log.candidate_id:
        candidate = await session.get(Candidate, log.candidate_id)
        if candidate:
            candidate_name = candidate.full_name or "Candidate"

    if log.application_id:
        app = await session.get(CandidateApplication, log.application_id)
        if app:
            job = await session.get(JobRequirement, app.job_requirement_id)
            if job:
                job_title = job.title

    reply = await hiring_agent_service.generate_smart_reply(log.body, candidate_name, job_title)
    return {"reply": reply}


@router.post("/send")
async def send_emails(
    request: EmailSendRequest,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.communications, PermissionAction.moderate))
    ],
    _background_tasks: BackgroundTasks,
) -> dict[str, int]:
    """Send emails."""
    template = None
    company_id = getattr(current_user, "company_id", None)
    if request.template_id:
        stmt = select(EmailTemplate).where(
            EmailTemplate.id == request.template_id, EmailTemplate.company_id == company_id
        )
        result = await session.execute(stmt)
        template = result.scalar_one_or_none()
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")

    recipients: list[tuple[str, Candidate | None]] = []

    if request.recipient_ids:
        stmt_cand = select(Candidate).where(
            Candidate.id.in_(request.recipient_ids), Candidate.company_id == company_id
        )
        res_cand = await session.execute(stmt_cand)
        for cand in res_cand.scalars().all():
            recipients.append((str(cand.email), cand))

    if request.recipient_emails:
        for email_addr in request.recipient_emails:
            stmt_e = (
                select(Candidate)
                .where(Candidate.email == email_addr, Candidate.company_id == company_id)
                .limit(1)
            )
            res_e = await session.execute(stmt_e)
            cand_e = res_e.scalar_one_or_none()

            if not any(r[0] == email_addr for r in recipients):
                recipients.append((email_addr, cand_e))

    if not recipients:
        raise HTTPException(status_code=400, detail="No recipients provided")

    default_stmt = select(Company).where(Company.id == company_id).limit(1)
    res_def = await session.execute(default_stmt)
    default_company = res_def.scalar_one_or_none()

    company_name_base = default_company.name if default_company else "Our Company"
    company_address_base = default_company.location if default_company and default_company.location else ""
    first_name = getattr(current_user, "first_name", "")
    last_name = getattr(current_user, "last_name", "")
    recruiter_name = (f"{first_name} {last_name}").strip() or "Recruiting Team"

    sent_count = 0
    failed_count = 0

    for email_addr, candidate in recipients:
        subject = request.subject or (template.subject if template else "Recruitment Update")
        body_template = request.body or (template.body if template else "No content")

        job_title = "the position"
        company_name = company_name_base
        company_address = company_address_base
        company_logo = default_company.logo_url if default_company else None

        if candidate:
            stmt_app = (
                select(CandidateApplication, JobRequirement, Company)
                .join(JobRequirement, CandidateApplication.job_requirement_id == JobRequirement.id)
                .outerjoin(Company, JobRequirement.company_id == Company.id)
                .where(CandidateApplication.candidate_id == candidate.id)
                .order_by(CandidateApplication.created_at.desc())
                .limit(1)
            )

            res_app = await session.execute(stmt_app)
            app_data = res_app.first()
            if app_data:
                _, job, company = app_data
                job_title = job.title
                if company:
                    company_name = company.name
                    company_address = company.location or ""
                    company_logo = company.logo_url

        candidate_name = (
            getattr(candidate, "full_name", candidate.email if candidate else email_addr) or "Candidate"
        )

        replacements = {
            "{{candidate_name}}": str(candidate_name),
            "{{job_title}}": str(job_title),
            "{{company_name}}": str(company_name),
            "{{recruiter_name}}": str(recruiter_name),
            "{{company_address}}": str(company_address),
            "{{company_logo}}": str(company_logo or ""),
            "{{frontend_url}}": str(_settings.frontend_url),
        }

        if request.custom_variables:
            for key, val in request.custom_variables.items():
                k = key if key.startswith("{{") else f"{{{{{key}}}}}"
                replacements[k] = str(val)

        final_body = body_template
        final_subject = subject
        for key, val in replacements.items():
            final_body = final_body.replace(key, val)
            final_subject = final_subject.replace(key, val)

        log_entry = EmailLog(
            recipient_email=email_addr,
            subject=final_subject,
            body=final_body,
            status="pending",
            template_id=template.id if template else None,
            candidate_id=candidate.id if candidate else None,
            company_id=company_id,
            sent_at=cast("Any", datetime.now()),
        )
        session.add(log_entry)
        await session.flush()

        success, error = await run_in_threadpool(
            send_smtp_email, email_addr, final_subject, final_body, company_name, company_logo
        )

        log_entry.status = "sent" if success else "failed"
        log_entry.error_message = error

        if success:
            sent_count += 1
        else:
            failed_count += 1

    await session.commit()
    return {"sent": sent_count, "failed": failed_count}


@router.post("/draft")
async def draft_email(
    request: EmailDraftRequest,
    _current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.communications, PermissionAction.generate))
    ],
) -> dict[str, Any]:
    """Draft an email using AI."""
    if not _settings.openai_api_key:
        return {"subject": f"Regarding {request.purpose}", "body": "Draft content."}

    try:
        client = AsyncOpenAI(api_key=str(_settings.openai_api_key))
        prompt = f"Draft a {request.tone} email for {request.purpose}."
        response = await client.chat.completions.create(
            model="gpt-3.5-turbo", messages=[{"role": "user", "content": prompt}]
        )
        return {"content": response.choices[0].message.content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/generate-template")
async def generate_template(
    request: TemplateGenerationRequest,
    _current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.communications, PermissionAction.generate))
    ],
) -> dict[str, Any]:
    """Generate a complete email template (name, subject, body) using AI."""
    if not _settings.openai_api_key:
        fallback_body = (
            "Dear {{candidate_name}},\n\n"
            "We are reaching out regarding {{job_title}} at {{company_name}}.\n\n"
            "Best regards,\n{{recruiter_name}}"
        )
        return {
            "name": request.purpose,
            "subject": "Regarding your application - {{job_title}}",
            "body": fallback_body,
            "variables": ["candidate_name", "job_title", "company_name", "recruiter_name"],
        }

    try:
        client = AsyncOpenAI(api_key=str(_settings.openai_api_key))
        system_prompt = (
            "You are an expert HR email writer. "
            "Generate a complete email template in JSON format with the following keys: "
            '"name" (short template name), "subject" (email subject line), '
            '"body" (full HTML-friendly email body). '
            "Use double-brace placeholders like {{candidate_name}}, {{job_title}}, {{company_name}}, "
            "{{recruiter_name}}, {{company_address}} where appropriate. "
            "Return ONLY valid JSON, no markdown fences."
        )
        user_prompt = f"Goal: {request.purpose}\nTone: {request.tone}"

        response = await client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            temperature=0.7,
        )

        raw = response.choices[0].message.content or ""
        raw = re.sub(r"^```[a-z]*\n?", "", raw.strip(), flags=re.IGNORECASE)
        raw = re.sub(r"```$", "", raw.strip())

        data = json.loads(raw)

        variables = re.findall(r"\{\{(\w+)\}\}", data.get("body", ""))
        variables = list(dict.fromkeys(variables))

        return {
            "name": data.get("name", request.purpose),
            "subject": data.get("subject", ""),
            "body": data.get("body", ""),
            "variables": variables,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Template generation failed: {e!s}") from e
