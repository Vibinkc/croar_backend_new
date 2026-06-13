import asyncio
import logging
import os
import re
import uuid
from datetime import date, datetime, time, timedelta
from typing import Any, cast

from fastapi.concurrency import run_in_threadpool
from google.oauth2 import service_account
from googleapiclient.discovery import build
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.email_templates import wrap_in_celebratory_template
from app.core.settings import settings as _settings
from app.models.enterprise.candidate import Candidate, CandidateApplication
from app.models.enterprise.communication import EmailLog, EmailTemplate
from app.models.enterprise.company import Company
from app.models.enterprise.interview import InterviewAutomation, InterviewSchedule
from app.models.enterprise.job import JobRequirement
from app.models.enterprise.user_role import EnterpriseUser as HiringAgent
from app.router.enterprise.communication import send_smtp_email

logger = logging.getLogger(__name__)
background_tasks = set()


async def generate_google_meet_link(
    start_time: datetime, end_time: datetime, candidate_email: str, interviewer_email: str, job_title: str
) -> str | None:
    """Generates a real Google Meet link using the Service Account."""
    try:
        # Assumes google_credentials.json is in the backend root directory
        creds_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
            "google_credentials.json",
        )
        if not os.path.exists(creds_path):
            logger.warning("Google credentials not found. Cannot generate Google Meet link.")
            return None

        creds = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
            creds_path, scopes=["https://www.googleapis.com/auth/calendar"]
        )
        service = build("calendar", "v3", credentials=creds)

        event: dict[str, object] = {
            "summary": f"Interview: {job_title}",
            "start": {"dateTime": start_time.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "Asia/Kolkata"},
            "end": {"dateTime": end_time.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "Asia/Kolkata"},
            "attendees": [{"email": candidate_email}, {"email": interviewer_email}],
            "conferenceData": {
                "createRequest": {
                    "requestId": f"meet-{uuid.uuid4().hex}",
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            },
        }

        # We run the synchronous Google API call in a threadpool
        def _create_event() -> dict[str, Any]:
            return cast(
                "dict[str, Any]",
                service.events().insert(calendarId="primary", body=event, conferenceDataVersion=1).execute(),
            )

        created_event = await run_in_threadpool(_create_event)
        meet_link = cast("str | None", created_event.get("hangoutLink"))

        if meet_link:
            return meet_link

    except Exception as e:
        logger.error(f"Failed to generate Google Meet link: {e}")

    return None


def parse_time(time_str: str) -> time:
    """Parses a string like '09:00' to a datetime.time object."""
    try:
        parts = time_str.split(":")
        return time(hour=int(parts[0]), minute=int(parts[1]))
    except Exception:
        return time(hour=9, minute=0)


async def find_available_slot(db: AsyncSession, automation: InterviewAutomation) -> datetime:
    """
    Finds the next available time slot starting from 'tomorrow', respecting
    daily_limits, working hours, and date ranges.
    Assumes each interview is 30 minutes.
    """
    today = date.today()
    start_d = today + timedelta(days=1)

    if automation.start_date:
        parsed_start = automation.start_date
        if parsed_start:
            start_d = max(start_d, parsed_start)

    end_d = start_d + timedelta(days=60)  # safety max
    if automation.end_date:
        parsed_end = automation.end_date
        if parsed_end and parsed_end >= start_d:
            end_d = parsed_end

    target_date = start_d

    start_t = parse_time(automation.start_time)
    end_t = parse_time(automation.end_time)

    # Guard against 0/None config that would otherwise hang the slot loop or disable
    # conflict detection / daily caps. Defaults match the previous "30-min, 1/day" intent.
    duration = max(1, automation.duration or 30)
    daily_limit = max(1, automation.daily_limit or 1)

    # NEW: if time_slots provided, use them instead of arbitrary 30-min increments
    custom_times: list[time] = []
    if automation.time_slots:
        custom_times = [parse_time(t) for t in automation.time_slots]
        custom_times.sort()

    days_checked = 0
    while target_date <= end_d and days_checked < 60:
        days_checked += 1
        # Skip weekends. 5 and 6 are Sat/Sun
        if target_date.weekday() >= 5:
            target_date += timedelta(days=1)
            continue

        # Count interviews already scheduled for this automation on `target_date`
        start_of_day = datetime.combine(target_date, time.min)
        end_of_day = datetime.combine(target_date, time.max)

        count_stmt = select(func.count(InterviewSchedule.id)).where(
            and_(
                InterviewSchedule.automation_id == automation.id,
                InterviewSchedule.scheduled_time >= start_of_day,
                InterviewSchedule.scheduled_time <= end_of_day,
            )
        )
        count_res = await db.execute(count_stmt)
        existing_count = count_res.scalar() or 0

        if existing_count < daily_limit:
            # Get existing times today for this automation to avoid clashes
            times_stmt = (
                select(InterviewSchedule.scheduled_time)
                .where(
                    and_(
                        InterviewSchedule.automation_id == automation.id,
                        InterviewSchedule.scheduled_time >= start_of_day,
                        InterviewSchedule.scheduled_time <= end_of_day,
                    )
                )
                .order_by(InterviewSchedule.scheduled_time)
            )
            times_res = await db.execute(times_stmt)
            existing_times = [cast("datetime", r[0]) for r in times_res.all()]

            if custom_times:
                for ct in custom_times:
                    candidate_slot_dt = datetime.combine(target_date, ct)
                    conflict = False
                    for et in existing_times:
                        if abs((et - candidate_slot_dt).total_seconds()) < (duration * 60):
                            conflict = True
                            break
                    if not conflict:
                        return candidate_slot_dt
            else:
                candidate_slot_dt = datetime.combine(target_date, start_t)
                end_limit_dt = datetime.combine(target_date, end_t)

                while candidate_slot_dt + timedelta(minutes=duration) <= end_limit_dt:
                    conflict = False
                    for et in existing_times:
                        if abs((et - candidate_slot_dt).total_seconds()) < (duration * 60):
                            conflict = True
                            break

                    if not conflict:
                        return candidate_slot_dt

                    candidate_slot_dt += timedelta(minutes=duration)

        target_date += timedelta(days=1)

    if custom_times:
        return datetime.combine(end_d + timedelta(days=1), custom_times[0])
    return datetime.combine(end_d + timedelta(days=1), start_t)


async def schedule_candidate_interview(
    db: AsyncSession, application: CandidateApplication, automation: InterviewAutomation
) -> InterviewSchedule | None:
    """
    Schedules an interview for a candidate based on the automation rules.
    """
    # Check if already scheduled
    existing_stmt = (
        select(InterviewSchedule)
        .where(
            and_(
                InterviewSchedule.application_id == application.id,
                InterviewSchedule.automation_id == automation.id,
            )
        )
        .limit(1)
    )
    existing_res = await db.execute(existing_stmt)
    if existing_res.scalar_one_or_none():
        return None

    # Fetch Candidate and Job
    cand_stmt = select(Candidate).where(Candidate.id == application.candidate_id)
    cand_res = await db.execute(cand_stmt)
    candidate = cand_res.scalar_one_or_none()

    job_stmt = select(JobRequirement).where(JobRequirement.id == application.job_requirement_id)
    job_res = await db.execute(job_stmt)
    job = job_res.scalar_one_or_none()

    if not candidate or not job:
        return None

    next_slot = await find_available_slot(db, automation)
    end_slot = next_slot + timedelta(minutes=automation.duration)

    # Determine interviewer email
    recruiter_email = str(_settings.mailer_sender_email)
    if automation.interviewer_email:
        recruiter_email = automation.interviewer_email
    else:
        agent_stmt = select(HiringAgent).limit(1)
        agent_res = await db.execute(agent_stmt)
        agent_found = agent_res.scalar_one_or_none()
        if agent_found and agent_found.email:
            recruiter_email = agent_found.email

    if automation.interview_type == "AI":
        base_url = _settings.frontend_url
        meet_link: str | None = f"{base_url}/interview/ai/{application.id}"
    elif automation.google_meet_link:
        meet_link = automation.google_meet_link
    else:
        meet_link = await generate_google_meet_link(
            start_time=next_slot,
            end_time=end_slot,
            candidate_email=str(candidate.email),
            interviewer_email=recruiter_email,
            job_title=job.title,
        )

    schedule = InterviewSchedule(
        automation_id=automation.id,
        application_id=application.id,
        interview_id=automation.interview_template_id if automation.interview_type == "AI" else None,
        scheduled_time=cast("Any", next_slot),
        meeting_link=meet_link,
        status="SCHEDULED",
        company_id=application.company_id,
    )
    db.add(schedule)
    await db.commit()

    # Trigger email invites
    await send_interview_invite(db, application, automation, schedule)

    return schedule


async def send_interview_invite(
    db: AsyncSession,
    application: CandidateApplication,
    automation: InterviewAutomation,
    schedule: InterviewSchedule,
) -> None:
    # Fetch Candidate
    cand_stmt = select(Candidate).where(Candidate.id == application.candidate_id)
    cand_res = await db.execute(cand_stmt)
    candidate = cand_res.scalar_one_or_none()

    # Fetch Job
    job_stmt = select(JobRequirement).where(JobRequirement.id == application.job_requirement_id)
    job_res = await db.execute(job_stmt)
    job = job_res.scalar_one_or_none()

    if not candidate or not job:
        return

    subject = f"Interview Scheduled: {job.title}"
    scheduled_time_str = (
        schedule.scheduled_time.strftime("%Y-%m-%d %H:%M") if schedule.scheduled_time else "TBD"
    )
    body = (
        f"Hello {candidate.full_name or 'Candidate'},\n\n"
        f"Your interview for {job.title} has been scheduled.\n\n"
        f"Time: {scheduled_time_str}\n"
        f"Link: {schedule.meeting_link}\n\n"
        "Best regards,\nHiring Team"
    )

    if automation.email_template_id:
        tpl_stmt = select(EmailTemplate).where(EmailTemplate.id == automation.email_template_id)
        res_tpl = await db.execute(tpl_stmt)
        template = res_tpl.scalar_one_or_none()
        if template:
            comp_stmt = select(Company).where(Company.id == application.company_id)
            comp_res = await db.execute(comp_stmt)
            company = comp_res.scalar_one_or_none()
            company_name = company.name if company else "Our Company"

            agent_stmt = select(HiringAgent).where(HiringAgent.company_id == application.company_id).limit(1)
            agent_res = await db.execute(agent_stmt)
            agent = agent_res.scalar_one_or_none()
            recruiter_name = (
                f"{agent.first_name} {agent.last_name or ''}".strip() if agent else "Recruiting Team"
            )

            replacements: dict[str, str] = {
                "candidate_name": candidate.full_name or "Candidate",
                "job_title": job.title,
                "company_name": company_name,
                "recruiter_name": recruiter_name,
                "meeting_link": str(schedule.meeting_link),
                "interview_link": str(schedule.meeting_link),
                "interview_time": scheduled_time_str,
            }

            subject = template.subject or subject
            body = template.body or body

            for key, val in replacements.items():
                for placeholder in [f"{{{{{key}}}}}", f"{{{{ {key} }}}}"]:
                    subject = subject.replace(placeholder, str(val))
                    body = body.replace(placeholder, str(val))

            button_pattern = r"\[(https?://[^\s\]]+)\]([^\n\r<]+)"

            def make_button(m: Any) -> str:
                url = m.group(1)
                label = m.group(2).strip()
                return (
                    f'<center><a href="{url}" style="display:inline-block;'
                    "padding:14px 30px;background-color:#6e8efb;color:#ffffff;"
                    "text-decoration:none;border-radius:8px;font-weight:bold;"
                    f'margin:20px 0;">{label}</a></center>'
                )

            body = re.sub(button_pattern, make_button, str(body))

            if str(schedule.meeting_link) not in str(body):
                body = str(body) + f"\n\n---\nJoin the meeting here: {schedule.meeting_link}"

            body_html = body.replace("\n", "<br>")
            body = wrap_in_celebratory_template(body_html, title="Interview Invitation!")

    # Save EmailLogs
    log_candidate = EmailLog(
        candidate_id=candidate.id,
        application_id=application.id,
        recipient_email=str(candidate.email),
        sender_email=_settings.mailer_sender_email,
        subject=subject,
        body=body,
        status="SENT",
        direction="outbound",
        sent_at=cast("Any", datetime.now()),
        company_id=application.company_id,
    )
    db.add(log_candidate)

    # Send to Interviewer (Recruiter)
    recruiter_email = str(_settings.mailer_sender_email)
    if automation.interviewer_email:
        recruiter_email = automation.interviewer_email
    else:
        agent_stmt = select(HiringAgent).where(HiringAgent.company_id == application.company_id).limit(1)
        agent_res = await db.execute(agent_stmt)
        agent_found = agent_res.scalar_one_or_none()
        if agent_found and agent_found.email:
            recruiter_email = agent_found.email

    log_recruiter = EmailLog(
        candidate_id=candidate.id,
        application_id=application.id,
        recipient_email=recruiter_email,
        sender_email=_settings.mailer_sender_email,
        subject=f"[Interviewer] {subject}",
        body=f"You have an upcoming interview with {candidate.full_name} ({candidate.email}).\n\n{body}",
        status="SENT",
        direction="outbound",
        sent_at=cast("Any", datetime.now()),
        company_id=application.company_id,
    )
    db.add(log_recruiter)

    await db.commit()

    def do_send_emails() -> None:
        try:
            send_smtp_email(str(candidate.email), subject, body)
            send_smtp_email(
                recruiter_email,
                f"[Interviewer] {subject}",
                f"You have an upcoming interview with {candidate.full_name} ({candidate.email}).\n\n{body}",
            )
        except Exception:
            pass

    task = asyncio.create_task(run_in_threadpool(do_send_emails))
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
