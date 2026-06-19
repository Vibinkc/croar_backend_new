import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.models.enterprise.job import JobRequirement
from app.services.enterprise.hiring_agent import hiring_agent_service
from app.services.enterprise.onboarding_service import initiate_onboarding_process

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

# Set up logging
logger = logging.getLogger(__name__)


def _company_id(config: RunnableConfig, fallback: str | None = None) -> str:
    """The acting company comes from the authenticated caller (injected into the graph
    config), never from an LLM argument — so the Pilot can't act on another tenant."""
    cid = config.get("configurable", {}).get("company_id") or fallback
    if not cid:
        raise ValueError("No company_id in agent context")
    return str(cid)


def _clamp_int(value: Any, lo: int, hi: int, default: int) -> int:
    """Coerce to int and clamp into [lo, hi]; fall back to default on bad input
    (the LLM may pass strings, floats, None, or out-of-range values as tool args)."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _norm_interview_type(value: str | None) -> str:
    """Normalize any human/panel/meet-ish value to GMEET, otherwise AI."""
    return (
        "GMEET"
        if (value or "AI").strip().upper()
        in {"GMEET", "HUMAN", "GOOGLE MEET", "GOOGLEMEET", "MEET", "PANEL", "LIVE"}
        else "AI"
    )


def _norm_assessment_type(value: str | None) -> Any:
    """Map to a valid AssessmentType (APTITUDE / CODING / BOTH); default BOTH on anything else."""
    from app.models.enterprise.assessment import AssessmentType

    try:
        return AssessmentType((value or "BOTH").strip().upper())
    except ValueError:
        return AssessmentType.BOTH


def _parse_date(value: str | None) -> "date | None":
    """Parse an ISO date string (YYYY-MM-DD) into a date; return None if missing/invalid."""
    if not value:
        return None
    from datetime import date as _date

    try:
        return _date.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        return None


def _generate_time_slots(start: str, end: str, duration: int, limit: int) -> list[str]:
    """Build a list of interview start times from a daily window, mirroring the frontend's
    auto-generate so interviews show their configured slots (AI interviews otherwise show 0)."""
    try:
        sh, sm = (int(x) for x in start.split(":"))
        eh, em = (int(x) for x in end.split(":"))
    except (ValueError, AttributeError):
        return []
    start_mins, end_mins = sh * 60 + sm, eh * 60 + em
    interval = duration if duration > 0 else 30
    slots: list[str] = []
    for i in range(max(0, limit)):
        slot = start_mins + i * interval
        if slot + interval > end_mins:
            break
        slots.append(f"{slot // 60:02d}:{slot % 60:02d}")
    return slots


async def _ensure_email_template(
    session: "AsyncSession", company_id: str, name: str, category: str, subject: str, body: str
) -> "UUID":
    """Find (by name) or create a reusable email template for the company, so automations
    actually SEND. Without an email_template_id the engine silently skips the email."""
    from sqlalchemy import select

    from app.models.enterprise.communication import EmailTemplate

    stmt = (
        select(EmailTemplate)
        .where(EmailTemplate.company_id == UUID(company_id), EmailTemplate.name == name)
        .limit(1)
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing:
        return existing.id
    tpl = EmailTemplate(name=name, subject=subject, body=body, category=category, company_id=UUID(company_id))
    session.add(tpl)
    await session.flush()
    return tpl.id


def _default_onboarding_form() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Canonical onboarding form: sections (each {id,title,fields[{name,label,type,required}]})
    that the editor counts and the candidate form renders, plus the required documents list."""
    sections: list[dict[str, Any]] = [
        {
            "id": "personal_info",
            "title": "Personal Information",
            "fields": [
                {"name": "full_name", "label": "Full Name", "type": "text", "required": True},
                {"name": "dob", "label": "Date of Birth", "type": "date", "required": True},
                {"name": "phone", "label": "Phone Number", "type": "phone", "required": True},
                {"name": "personal_email", "label": "Personal Email", "type": "email", "required": True},
                {"name": "current_address", "label": "Current Address", "type": "text", "required": True},
            ],
        },
        {
            "id": "job_info",
            "title": "Job Information",
            "fields": [
                {"name": "job_title", "label": "Job Title", "type": "text", "required": True},
                {"name": "start_date", "label": "Preferred Start Date", "type": "date", "required": True},
            ],
        },
        {
            "id": "education_info",
            "title": "Education",
            "fields": [
                {"name": "highest_degree", "label": "Highest Degree", "type": "text", "required": True},
                {
                    "name": "university",
                    "label": "University / Institution",
                    "type": "text",
                    "required": False,
                },
                {
                    "name": "graduation_year",
                    "label": "Year of Graduation",
                    "type": "number",
                    "required": False,
                },
            ],
        },
        {
            "id": "bank_details",
            "title": "Bank Details",
            "fields": [
                {"name": "account_holder", "label": "Account Holder Name", "type": "text", "required": True},
                {"name": "account_number", "label": "Account Number", "type": "text", "required": True},
                {"name": "ifsc", "label": "IFSC / Routing Code", "type": "text", "required": True},
            ],
        },
        {
            "id": "documents",
            "title": "Document Uploads",
            "fields": [
                {
                    "name": "id_proof",
                    "label": "Government ID (Passport / Aadhar)",
                    "type": "file",
                    "required": True,
                },
                {"name": "address_proof", "label": "Address Proof", "type": "file", "required": True},
                {
                    "name": "education_cert",
                    "label": "Education Certificate",
                    "type": "file",
                    "required": True,
                },
                {"name": "resume", "label": "Updated Resume", "type": "file", "required": False},
            ],
        },
    ]
    required_documents: list[dict[str, Any]] = [
        {"name": "Government ID", "description": "Passport / Aadhar / National ID"},
        {"name": "Address Proof", "description": "Utility bill or rental agreement"},
        {"name": "Education Certificate", "description": "Highest qualification certificate"},
    ]
    return sections, required_documents


async def _ensure_onboarding_template(session: "AsyncSession", company_id: str, role_title: str) -> "UUID":
    """Find (by name) or create the role's onboarding template, populated with real sections,
    fields and required documents. Reusing by name avoids the unique-name crash on rebuild."""
    from sqlalchemy import select

    from app.models.enterprise.onboarding import OnboardingTemplate

    name = f"{role_title} · Onboarding"
    sections, required_documents = _default_onboarding_form()
    section_ids = [s["id"] for s in sections]
    form_config: dict[str, Any] = {"sections": sections}
    description = f"Onboarding checklist for {role_title}."

    stmt = (
        select(OnboardingTemplate)
        .where(OnboardingTemplate.company_id == UUID(company_id), OnboardingTemplate.name == name)
        .limit(1)
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing:
        existing.description = description
        existing.sections = section_ids
        existing.required_documents = required_documents
        existing.form_config = form_config
        await session.flush()
        return existing.id
    tpl = OnboardingTemplate(
        name=name,
        description=description,
        sections=section_ids,
        required_documents=required_documents,
        form_config=form_config,
        company_id=UUID(company_id),
    )
    session.add(tpl)
    await session.flush()
    return tpl.id


@tool
async def score_candidate_application(target_application_id: str, config: RunnableConfig) -> dict[str, Any]:
    """
    Evaluates a specific candidate application autonomously.
    'target_application_id' MUST be the UUID of the Candidate Application (NOT the company ID).
    """
    session: AsyncSession = config["configurable"]["session"]
    try:
        UUID(target_application_id)  # validate before hitting the service
    except (ValueError, AttributeError):
        return {"status": "error", "message": "Invalid application id — pass the application's UUID."}
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
            company_id=UUID(_company_id(config, target_company_id)),
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
    try:
        role_title = (role_title or "").strip()
        if not role_title:
            return {"status": "error", "message": "A role title is required."}
        jd_content = (jd_content or "").strip() or f"We are hiring a {role_title}."
        raw_skills = skills.split(",") if isinstance(skills, str) else (skills or [])
        skills = [f"{s}".strip() for s in raw_skills if f"{s}".strip()]
        min_exp = _clamp_int(min_exp, 0, 50, 0)
        max_exp = max(min_exp, _clamp_int(max_exp, 0, 60, 10))
        # Default rounds if none provided
        rounds = workflow_rounds or ["Screening", "Assessment", "Interview", "Offer", "Onboarding"]
        formatted_stages = [{"id": str(i + 1), "name": name, "order": i + 1} for i, name in enumerate(rounds)]

        new_job = JobRequirement(
            title=role_title,
            description=jd_content,
            company_id=UUID(_company_id(config, target_company_id)),
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


async def _get_company_job(session: "AsyncSession", company_id: str, job_id: str) -> Any:
    """Fetch a live (non-deleted) job by id, scoped to the company. Returns the job or None."""
    from sqlalchemy import select

    try:
        job_uuid = UUID(job_id)
    except (ValueError, AttributeError):
        return None
    stmt = select(JobRequirement).where(
        JobRequirement.id == job_uuid,
        JobRequirement.company_id == UUID(company_id),
        JobRequirement.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


@tool
async def list_jobs(config: RunnableConfig) -> dict[str, Any]:
    """List the company's active (non-deleted) jobs with their id, title and location. Use this
    FIRST to find the job the user refers to by name before updating or deleting it."""
    from sqlalchemy import select

    session: AsyncSession = config["configurable"]["session"]
    try:
        cid = _company_id(config)
        stmt = (
            select(JobRequirement)
            .where(JobRequirement.company_id == UUID(cid), JobRequirement.deleted_at.is_(None))
            .order_by(JobRequirement.created_at.desc())
            .limit(50)
        )
        jobs = (await session.execute(stmt)).scalars().all()
        return {
            "status": "success",
            "count": len(jobs),
            "jobs": [
                {"job_id": str(j.id), "title": j.title, "location": j.location, "active": j.status_id == 2}
                for j in jobs
            ],
        }
    except Exception as e:
        logger.error(f"Error listing jobs: {e}")
        return {"status": "error", "message": str(e)}


@tool
async def update_job(
    job_id: str,
    config: RunnableConfig,
    title: str | None = None,
    jd_content: str | None = None,
    location: str | None = None,
    skills: list[str] | None = None,
    min_exp: int | None = None,
    max_exp: int | None = None,
    is_active: bool | None = None,
) -> dict[str, Any]:
    """Update an existing job's fields. Pass ONLY the fields to change; omit the rest (they stay
    as-is). `is_active=False` moves the job back to Draft, True makes it LIVE. Find the job_id with
    list_jobs first if the user named the role."""
    session: AsyncSession = config["configurable"]["session"]
    try:
        cid = _company_id(config)
        job = await _get_company_job(session, cid, job_id)
        if not job:
            return {"status": "error", "message": "Job not found (or already deleted)."}
        changed: list[str] = []
        if title and title.strip():
            job.title = title.strip()
            changed.append("title")
        if jd_content and jd_content.strip():
            job.description = jd_content.strip()
            changed.append("description")
        if location and location.strip():
            job.location = location.strip()
            changed.append("location")
        if skills is not None:
            raw = skills.split(",") if isinstance(skills, str) else skills
            job.required_skills = [f"{s}".strip() for s in raw if f"{s}".strip()]
            changed.append("skills")
        if min_exp is not None:
            job.experience_min = _clamp_int(min_exp, 0, 50, 0)
            changed.append("experience_min")
        if max_exp is not None:
            job.experience_max = max(job.experience_min or 0, _clamp_int(max_exp, 0, 60, 10))
            changed.append("experience_max")
        if is_active is not None:
            job.status_id = 2 if is_active else 1
            changed.append("status")
        if not changed:
            return {"status": "no_change", "message": "Nothing to update — no fields were provided."}
        await session.commit()
        return {
            "status": "success",
            "job_id": str(job.id),
            "updated": changed,
            "message": f"Updated {', '.join(changed)} for '{job.title}'.",
        }
    except Exception as e:
        logger.error(f"Error updating job: {e}")
        return {"status": "error", "message": str(e)}


@tool
async def delete_job(job_id: str, config: RunnableConfig) -> dict[str, Any]:
    """Delete a job and its ENTIRE pipeline (assessment / mail / interview / onboarding
    automations) and remove its non-hired applications. HIRED candidates are preserved. This is
    DESTRUCTIVE — only call it AFTER the user has explicitly confirmed the exact job to delete.
    Find the job_id with list_jobs first."""
    from datetime import datetime

    from sqlalchemy import delete, update

    from app.models.enterprise.assessment import AssessmentAutomation
    from app.models.enterprise.candidate import CandidateApplication
    from app.models.enterprise.communication import MailAutomation
    from app.models.enterprise.interview import InterviewAutomation
    from app.models.enterprise.onboarding import OnboardingAutomation

    session: AsyncSession = config["configurable"]["session"]
    try:
        cid = _company_id(config)
        job = await _get_company_job(session, cid, job_id)
        if not job:
            return {"status": "error", "message": "Job not found (or already deleted)."}
        job_uuid = job.id
        for model in (AssessmentAutomation, MailAutomation, InterviewAutomation, OnboardingAutomation):
            await session.execute(delete(model).where(model.job_requirement_id == job_uuid))
        # Soft-delete non-hired applications (status_id 5 == Hired) so hired people are kept.
        await session.execute(
            update(CandidateApplication)
            .where(
                CandidateApplication.job_requirement_id == job_uuid,
                CandidateApplication.status_id != 5,
                CandidateApplication.deleted_at.is_(None),
            )
            .values(deleted_at=datetime.now())
        )
        job.deleted_at = datetime.now()
        await session.commit()
        return {
            "status": "success",
            "job_id": str(job_uuid),
            "message": f"Deleted '{job.title}' and its pipeline. Hired candidates were preserved.",
        }
    except Exception as e:
        logger.error(f"Error deleting job: {e}")
        try:
            await session.rollback()
        except Exception:
            pass
        return {"status": "error", "message": str(e)}


@tool
async def source_candidates(
    job_id: str, role: str, skills: str | None = None, count: int = 10, location: str | None = None
) -> dict[str, Any]:
    """Search LIVE candidate profiles for an existing job across all sourcing platforms. CALL THIS
    whenever the user wants to source / find candidates for a job (e.g. "source 10 candidates").
    Pass the job_id (from the build result or list_jobs), the `role` title, the key `skills`
    (comma-separated), `count` (how many to return), and `location` if given. Do NOT ask "how many"
    again if the user already gave a number — just call this with it. The UI renders the returned
    candidates as a checkbox list and handles sending the invites; you do NOT send invites yourself.
    After calling it, briefly tell the user to pick who to invite from the list."""
    from app.router.enterprise.sourcing import backfill_contacts, search_all_platforms

    count = _clamp_int(count, 1, 25, 10)
    query = " ".join(p.strip() for p in [role, skills] if p and p.strip())
    if not query:
        return {"status": "error", "message": "Tell me the role to search candidates for."}
    try:
        profiles = await search_all_platforms(query, location, page=1, page_size=min(max(count, 5), 15))
        profiles = await backfill_contacts(profiles, limit=min(count, 8))
        profiles.sort(key=lambda p: 0 if p.get("email") else 1)
        slim = [
            {
                "full_name": p.get("full_name"),
                "headline": p.get("headline"),
                "platform": p.get("platform"),
                "location": p.get("location"),
                "profile_url": p.get("profile_url"),
                "email": p.get("email"),
            }
            for p in profiles[:count]
        ]
        return {
            "status": "success",
            "ui": "candidate_picker",  # signals the Pilot UI to render the selectable list
            "job_id": job_id,
            "count": len(slim),
            "profiles": slim,
            "message": (
                f"Found {len(slim)} candidates for this role — pick who to invite from the list below."
                if slim
                else "No candidates found for this role. Try different/broader skills or location."
            ),
        }
    except Exception as e:
        logger.error(f"Error sourcing candidates: {e}")
        return {"status": "error", "message": "Candidate search failed — please try again."}


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


@tool
async def setup_assessment_automation(
    job_id: str,
    stage_index: int,
    config: RunnableConfig,
    assessment_type: str = "BOTH",
    topic: str = "Role fundamentals",
    question_count: int = 10,
    test_duration: int = 30,
    auto_move: bool = True,
) -> dict[str, Any]:
    """Arm an AUTOMATIC assessment for a job at a hiring stage. Candidates who reach
    `stage_index` are sent the test automatically. `assessment_type` is one of
    APTITUDE, CODING, or BOTH. Use after the job is created (pass its job_id)."""
    from app.models.enterprise.assessment import AssessmentAutomation

    session: AsyncSession = config["configurable"]["session"]
    try:
        cid = _company_id(config)
        try:
            job_uuid = UUID(job_id)
        except (ValueError, AttributeError):
            return {"status": "error", "message": "Invalid job_id — pass the job's UUID."}
        atype = _norm_assessment_type(assessment_type)
        stage_index = _clamp_int(stage_index, 0, 20, 2)
        question_count = _clamp_int(question_count, 1, 50, 10)
        test_duration = _clamp_int(test_duration, 5, 240, 30)
        topic = (topic or "").strip() or "Role fundamentals"
        email_tpl_id = await _ensure_email_template(
            session,
            cid,
            "Croar Pilot · Assessment Invite",
            "ASSESSMENT",
            "Your {{job_title}} assessment is ready",
            "<p>Hi {{candidate_name}},</p><p>You've reached the assessment stage for "
            "<strong>{{job_title}}</strong>. Please complete your {{topic}} assessment "
            "({{test_duration}} minutes):</p>"
            '<p><a href="{{assessment_link}}">Start Assessment</a></p><p>Good luck!</p>',
        )
        auto = AssessmentAutomation(
            job_requirement_id=job_uuid,
            stage_index=stage_index,
            stage_name=f"Assessment (Stage {stage_index})",
            criteria=f"Reached stage {stage_index}",
            type=atype,
            topic=topic,
            question_count=question_count,
            test_duration=test_duration,
            company_id=UUID(cid),
            email_template_id=email_tpl_id,
            is_enabled=True,
            is_immediate=True,
            auto_move=auto_move,
        )
        session.add(auto)
        await session.commit()
        await session.refresh(auto)
        return {
            "status": "success",
            "assessment_automation_id": str(auto.id),
            "message": f"{atype.value} assessment armed at stage {stage_index} ({test_duration} min).",
        }
    except Exception as e:
        logger.error(f"Error setting up assessment automation: {e}")
        return {"status": "error", "message": str(e)}


@tool
async def setup_interview_automation(
    job_id: str,
    stage_index: int,
    config: RunnableConfig,
    interview_type: str = "GMEET",
    duration: int = 30,
    daily_limit: int = 5,
) -> dict[str, Any]:
    """Arm an AUTOMATIC interview round for a job at a hiring stage. Candidates who reach
    `stage_index` are auto-scheduled. `interview_type` is GMEET (Google Meet) or AI."""
    from app.models.enterprise.interview import InterviewAutomation

    session: AsyncSession = config["configurable"]["session"]
    try:
        cid = _company_id(config)
        try:
            job_uuid = UUID(job_id)
        except (ValueError, AttributeError):
            return {"status": "error", "message": "Invalid job_id — pass the job's UUID."}
        itype = _norm_interview_type(interview_type)
        stage_index = _clamp_int(stage_index, 0, 20, 3)
        duration = _clamp_int(duration, 10, 240, 30)
        daily_limit = _clamp_int(daily_limit, 1, 50, 5)
        email_tpl_id = await _ensure_email_template(
            session,
            cid,
            "Croar Pilot · Interview Invite",
            "INTERVIEW",
            "Your {{job_title}} interview",
            "<p>Hi {{candidate_name}},</p><p>You're invited to your interview for "
            "<strong>{{job_title}}</strong>. Join here:</p>"
            '<p><a href="{{interview_link}}">Start Interview</a></p>',
        )
        auto = InterviewAutomation(
            job_requirement_id=job_uuid,
            stage_index=stage_index,
            stage_name=f"Interview (Stage {stage_index})",
            criteria=f"Reached stage {stage_index}",
            interview_type=itype,
            duration=duration,
            daily_limit=daily_limit,
            start_time="09:00",
            end_time="17:00",
            time_slots=_generate_time_slots("09:00", "17:00", duration, daily_limit),
            company_id=UUID(cid),
            email_template_id=email_tpl_id,
            is_enabled=True,
            auto_move=True,
        )
        session.add(auto)
        await session.commit()
        await session.refresh(auto)
        return {
            "status": "success",
            "interview_automation_id": str(auto.id),
            "message": f"{auto.interview_type} interview armed at stage {stage_index} ({duration} min).",
        }
    except Exception as e:
        logger.error(f"Error setting up interview automation: {e}")
        return {"status": "error", "message": str(e)}


@tool
async def setup_onboarding_automation(
    job_id: str, stage_index: int, config: RunnableConfig
) -> dict[str, Any]:
    """Arm AUTOMATIC onboarding for a job at its FINAL stage. Hired candidates who reach
    `stage_index` are moved into the onboarding flow automatically."""
    from app.models.enterprise.onboarding import OnboardingAutomation

    session: AsyncSession = config["configurable"]["session"]
    try:
        cid = _company_id(config)
        try:
            job_uuid = UUID(job_id)
        except (ValueError, AttributeError):
            return {"status": "error", "message": "Invalid job_id — pass the job's UUID."}
        stage_index = _clamp_int(stage_index, 0, 20, 5)
        email_tpl_id = await _ensure_email_template(
            session,
            cid,
            "Croar Pilot · Onboarding Welcome",
            "ONBOARDING",
            "Welcome to {{company_name}}!",
            "<p>Hi {{candidate_name}},</p><p>Congratulations &mdash; welcome aboard! "
            "Please complete your onboarding to get started.</p>",
        )
        auto = OnboardingAutomation(
            job_requirement_id=job_uuid,
            stage_index=stage_index,
            stage_name="Onboarding",
            company_id=UUID(cid),
            email_template_id=email_tpl_id,
            is_enabled=True,
            auto_move=False,
        )
        session.add(auto)
        await session.commit()
        await session.refresh(auto)
        return {
            "status": "success",
            "onboarding_automation_id": str(auto.id),
            "message": f"Onboarding armed at stage {stage_index}.",
        }
    except Exception as e:
        logger.error(f"Error setting up onboarding automation: {e}")
        return {"status": "error", "message": str(e)}


_MAIL_TEMPLATES = {
    "screening": (
        "Croar Pilot · Application Received",
        "We received your application for {{job_title}}",
        "<p>Hi {{candidate_name}},</p><p>Thanks for applying to <strong>{{job_title}}</strong> at "
        "{{company_name}}. Our team is reviewing your profile and you'll hear from us shortly.</p>",
    ),
    "offer": (
        "Croar Pilot · Offer",
        "Your offer for {{job_title}}",
        "<p>Hi {{candidate_name}},</p><p>Congratulations! We'd love to offer you the "
        "<strong>{{job_title}}</strong> role at {{company_name}}. Our team will share the details "
        "with you shortly.</p>",
    ),
    "rejection": (
        "Croar Pilot · Application Update",
        "Update on your {{job_title}} application",
        "<p>Hi {{candidate_name}},</p><p>Thank you for your interest in <strong>{{job_title}}</strong>. "
        "After careful review we won't be moving forward at this time. We genuinely wish you the best.</p>",
    ),
}


@tool
async def setup_mail_automation(
    job_id: str, stage_index: int, config: RunnableConfig, purpose: str = "screening", auto_move: bool = True
) -> dict[str, Any]:
    """Arm an AUTOMATIC stage email for a job. Use this for non-assessment/interview emails:
    `purpose='screening'` (application-received acknowledgement, usually stage 1) or
    `purpose='offer'` (offer email at the offer stage). `auto_move=True` advances the candidate
    to the next stage after the email — this is what chains the whole funnel together."""
    from app.models.enterprise.communication import MailAutomation

    session: AsyncSession = config["configurable"]["session"]
    try:
        cid = _company_id(config)
        try:
            job_uuid = UUID(job_id)
        except (ValueError, AttributeError):
            return {"status": "error", "message": "Invalid job_id — pass the job's UUID."}
        purpose = (purpose or "screening").strip().lower()
        stage_index = _clamp_int(stage_index, 0, 20, 1)
        name, subject, body = _MAIL_TEMPLATES.get(purpose, _MAIL_TEMPLATES["screening"])
        tpl_id = await _ensure_email_template(session, cid, name, "GENERAL", subject, body)
        auto = MailAutomation(
            job_requirement_id=job_uuid,
            stage_index=stage_index,
            stage_name=f"{purpose.title()} email (Stage {stage_index})",
            criteria=f"Reached stage {stage_index}",
            template_id=tpl_id,
            company_id=UUID(cid),
            is_enabled=True,
            is_immediate=True,
            auto_move=auto_move,
        )
        session.add(auto)
        await session.commit()
        await session.refresh(auto)
        return {
            "status": "success",
            "mail_automation_id": str(auto.id),
            "message": f"{purpose} email armed at stage {stage_index}.",
        }
    except Exception as e:
        logger.error(f"Error setting up mail automation: {e}")
        return {"status": "error", "message": str(e)}


@tool
async def build_hiring_pipeline(
    role_title: str,
    jd_content: str,
    config: RunnableConfig,
    location: str = "Remote",
    min_exp: int = 0,
    max_exp: int = 10,
    skills: list[str] | None = None,
    assessment_type: str = "BOTH",
    assessment_topic: str = "Role fundamentals",
    question_count: int = 10,
    test_duration: int = 30,
    interview_type: str = "AI",
    interview_duration: int = 30,
    interview_slots_per_day: int = 5,
    interviewer_email: str | None = None,
    interview_start_time: str = "09:00",
    interview_end_time: str = "17:00",
    interview_start_date: str | None = None,
    interview_end_date: str | None = None,
) -> dict[str, Any]:
    """Build the ENTIRE automated hiring pipeline for a role in ONE step. This is the FAST,
    PREFERRED way to set everything up — call it once instead of the individual create_job /
    setup_* tools. It creates the LIVE job (stages Screening -> Assessment -> Interview ->
    Offer -> Onboarding) and arms ALL automations in a single transaction: a screening
    acknowledgement email, the auto-sent assessment, the interview, the offer email,
    and onboarding — every step auto-advancing so candidates flow end-to-end with no manual work.
    `jd_content` is the full job description you write yourself. `assessment_type` is APTITUDE,
    CODING, or BOTH.
    INTERVIEW config: `interview_type` is "AI" (the AI conducts it automatically) or "GMEET"
    (a human interviewer on Google Meet). When "GMEET" you MUST pass `interviewer_email` (the
    human interviewer's email). `interview_slots_per_day` caps how many interviews per day;
    `interview_duration` is minutes per interview; `interview_start_time`/`interview_end_time`
    bound the daily window ("HH:MM").
    You must provide role_title and jd_content; everything else has sane defaults.
    Role-specific assessment and interview QUESTIONS are AI-generated and saved as real template
    records (visible in the Assessment / Interview / Onboarding Templates tabs)."""
    import asyncio

    from app.models.enterprise.assessment import AssessmentAutomation, AssessmentTemplate
    from app.models.enterprise.communication import MailAutomation
    from app.models.enterprise.interview import Interview, InterviewAutomation
    from app.models.enterprise.onboarding import OnboardingAutomation
    from app.services.enterprise.ai_service import (
        generate_assessment_questions,
        generate_interview_questions_service,
    )

    session: AsyncSession = config["configurable"]["session"]
    try:
        cid = _company_id(config)
        company_uuid = UUID(cid)

        # --- Normalize / validate LLM-supplied inputs (defensive against bad tool args). ---
        role_title = (role_title or "").strip()
        if not role_title:
            return {"status": "error", "message": "A role title is required to build the pipeline."}
        jd_content = (jd_content or "").strip() or f"We are hiring a {role_title}. Join our team!"
        # skills may arrive as a comma-separated string instead of a list (or contain non-strings).
        raw_skills = skills.split(",") if isinstance(skills, str) else (skills or [])
        skills = [f"{s}".strip() for s in raw_skills if f"{s}".strip()]
        min_exp = _clamp_int(min_exp, 0, 50, 0)
        max_exp = max(min_exp, _clamp_int(max_exp, 0, 60, 10))
        question_count = _clamp_int(question_count, 1, 50, 10)
        test_duration = _clamp_int(test_duration, 5, 240, 30)
        interview_duration = _clamp_int(interview_duration, 10, 240, 30)
        interview_slots_per_day = _clamp_int(interview_slots_per_day, 1, 50, 5)
        interviewer_email = (interviewer_email or "").strip() or None
        interview_type = _norm_interview_type(interview_type)
        atype = _norm_assessment_type(assessment_type)

        # 1. LIVE job with the full 5-stage funnel.
        rounds = ["Screening", "Assessment", "AI Interview", "Offer", "Onboarding"]
        stages = [{"id": str(i + 1), "name": n, "order": i + 1} for i, n in enumerate(rounds)]
        job = JobRequirement(
            title=role_title,
            description=jd_content,
            company_id=company_uuid,
            location=location,
            experience_min=min_exp,
            experience_max=max_exp,
            required_skills=skills or [],
            status_id=2,  # ACTIVE / LIVE
            workflow_stages=stages,
        )
        session.add(job)
        await session.flush()  # populate job.id without committing yet
        job_id = job.id

        # Derive interview difficulty from seniority, then AI-generate the role-specific
        # assessment + interview questions CONCURRENTLY (these are the slow LLM calls).
        difficulty = "Advanced" if min_exp >= 5 else "Intermediate" if min_exp >= 2 else "Beginner"
        interview_topic = f"{role_title} ({', '.join(skills)})" if skills else role_title
        assess_questions, interview_questions = await asyncio.gather(
            generate_assessment_questions(atype, assessment_topic, question_count),
            generate_interview_questions_service(interview_topic, 8, difficulty),
        )

        # 2. Mail: screening acknowledgement (stage 1) -> auto-advances to Assessment.
        s_name, s_subj, s_body = _MAIL_TEMPLATES["screening"]
        screen_tpl = await _ensure_email_template(session, cid, s_name, "GENERAL", s_subj, s_body)
        session.add(
            MailAutomation(
                job_requirement_id=job_id,
                stage_index=1,
                stage_name="Screening email",
                criteria="Reached stage 1",
                template_id=screen_tpl,
                company_id=company_uuid,
                is_enabled=True,
                is_immediate=True,
                auto_move=True,
            )
        )

        # 3. Assessment (stage 2) -> auto-sends test, auto-advances.
        assess_tpl = await _ensure_email_template(
            session,
            cid,
            "Croar Pilot · Assessment Invite",
            "ASSESSMENT",
            "Your {{job_title}} assessment is ready",
            "<p>Hi {{candidate_name}},</p><p>You've reached the assessment stage for "
            "<strong>{{job_title}}</strong>. Please complete your {{topic}} assessment "
            "({{test_duration}} minutes):</p>"
            '<p><a href="{{assessment_link}}">Start Assessment</a></p><p>Good luck!</p>',
        )
        # Real, role-specific Assessment Template (shows in the Assessment Templates tab).
        assess_template = AssessmentTemplate(
            name=f"{role_title} · Assessment",
            type=atype,
            topic=assessment_topic,
            question_count=question_count,
            generated_questions=assess_questions,
            test_duration=test_duration,
            email_template_id=assess_tpl,
            company_id=company_uuid,
        )
        session.add(assess_template)
        await session.flush()
        session.add(
            AssessmentAutomation(
                job_requirement_id=job_id,
                stage_index=2,
                stage_name="Assessment",
                criteria="Reached stage 2",
                type=atype,
                topic=assessment_topic,
                question_count=question_count,
                generated_questions=assess_questions,  # used directly when sending
                test_duration=test_duration,
                template_id=assess_template.id,  # link to the saved template
                company_id=company_uuid,
                email_template_id=assess_tpl,
                is_enabled=True,
                is_immediate=True,
                auto_move=True,
            )
        )

        # 4. Interview (stage 3) -> AI-conducted or human (GMEET), auto-advances.
        itype = (interview_type or "AI").upper()
        is_ai = itype == "AI"
        intv_tpl = await _ensure_email_template(
            session,
            cid,
            "Croar Pilot · Interview Invite",
            "INTERVIEW",
            "Your {{job_title}} interview",
            "<p>Hi {{candidate_name}},</p><p>You're invited to your interview for "
            "<strong>{{job_title}}</strong>. Join here:</p>"
            '<p><a href="{{interview_link}}">Start Interview</a></p>',
        )
        # Real, role-specific Interview Template (shows in the Interview Templates tab).
        interview_template = Interview(
            title=f"{role_title} · Interview",
            description=(
                f"AI-conducted interview for {role_title}."
                if is_ai
                else f"Human interview for {role_title} (interviewer: {interviewer_email})."
            ),
            topic=interview_topic,
            duration=interview_duration,
            difficulty=difficulty,
            require_video=True,
            type="TECHNICAL",
            plan={"questions": interview_questions},
            is_active=True,
            company_id=company_uuid,
        )
        session.add(interview_template)
        await session.flush()
        session.add(
            InterviewAutomation(
                job_requirement_id=job_id,
                stage_index=3,
                stage_name="AI Interview" if is_ai else "Interview",
                criteria="Reached stage 3",
                interview_type=itype,
                interview_template_id=interview_template.id,  # link to the saved template
                interviewer_email=None if is_ai else interviewer_email,
                duration=interview_duration,
                daily_limit=interview_slots_per_day,
                start_time=interview_start_time,
                end_time=interview_end_time,
                start_date=_parse_date(interview_start_date),
                end_date=_parse_date(interview_end_date),
                time_slots=_generate_time_slots(
                    interview_start_time, interview_end_time, interview_duration, interview_slots_per_day
                ),
                company_id=company_uuid,
                email_template_id=intv_tpl,
                is_enabled=True,
                auto_move=True,
            )
        )

        # 5. Mail: offer (stage 4) -> auto-advances to Onboarding.
        o_name, o_subj, o_body = _MAIL_TEMPLATES["offer"]
        offer_tpl = await _ensure_email_template(session, cid, o_name, "GENERAL", o_subj, o_body)
        session.add(
            MailAutomation(
                job_requirement_id=job_id,
                stage_index=4,
                stage_name="Offer email",
                criteria="Reached stage 4",
                template_id=offer_tpl,
                company_id=company_uuid,
                is_enabled=True,
                is_immediate=True,
                auto_move=True,
            )
        )

        # 6. Onboarding (stage 5).
        onb_tpl = await _ensure_email_template(
            session,
            cid,
            "Croar Pilot · Onboarding Welcome",
            "ONBOARDING",
            "Welcome to {{company_name}}!",
            "<p>Hi {{candidate_name}},</p><p>Congratulations &mdash; welcome aboard! "
            "Please complete your onboarding to get started.</p>",
        )
        # Real, role-specific Onboarding Template (find-or-reuse by name -> no unique-name crash
        # on rebuild; populated with real sections/fields/documents the UI + candidate form use).
        onboarding_template_id = await _ensure_onboarding_template(session, cid, role_title)
        session.add(
            OnboardingAutomation(
                job_requirement_id=job_id,
                stage_index=5,
                stage_name="Onboarding",
                template_id=onboarding_template_id,  # link to the saved template
                company_id=company_uuid,
                email_template_id=onb_tpl,
                is_enabled=True,
                auto_move=False,
            )
        )

        # ONE commit for the whole pipeline.
        await session.commit()
        return {
            "status": "success",
            "ui": "pipeline_built",  # signals the Pilot UI to render the actionable result card
            "job_id": str(job_id),
            "role": role_title,
            "armed": [
                "Screening acknowledgement email",
                f"{atype.value} assessment with {len(assess_questions)} generated questions",
                (
                    f"AI interview with {len(interview_questions)} generated questions"
                    if is_ai
                    else f"Human interview (interviewer: {interviewer_email}, "
                    f"{interview_slots_per_day}/day) with {len(interview_questions)} questions"
                ),
                "Offer email",
                "Onboarding template",
            ],
            "templates_created": {
                "assessment_template": f"{role_title} · Assessment",
                "interview_template": f"{role_title} · Interview",
                "onboarding_template": f"{role_title} · Onboarding",
            },
            "message": (
                f"Live job '{role_title}' created with the full automated pipeline armed and "
                "role-specific Assessment, Interview, and Onboarding templates generated "
                "(questions included) — all auto-advancing."
            ),
        }
    except Exception as e:
        logger.error(f"Error building hiring pipeline: {e}")
        try:
            await session.rollback()
        except Exception:
            pass
        return {"status": "error", "message": str(e)}
