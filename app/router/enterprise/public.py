import contextlib
import json
import traceback
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.dependencies import DBSessionDep
from app.models.enterprise.candidate import Candidate, CandidateApplication
from app.models.enterprise.job import JobRequirement, JobStatus
from app.schemas.enterprise.jobs import JobRequirementResponse

router = APIRouter(prefix="/public/jobs", tags=["Public Jobs"])


@router.get("/list", response_model=list[JobRequirementResponse])
async def list_active_jobs(
    session: DBSessionDep, company_id: UUID | None = None, company_slug: str | None = None
) -> list[JobRequirement]:
    """Publicly list active jobs for a specific company."""
    if not company_id and not company_slug:
        return []

    stmt = (
        select(JobRequirement)
        .join(JobStatus)
        .where(JobStatus.name == "OPEN", JobRequirement.deleted_at.is_(None))
        .options(selectinload(JobRequirement.company), selectinload(JobRequirement.postings))
    )

    if company_id:
        stmt = stmt.where(JobRequirement.company_id == company_id)
    if company_slug:
        from app.models.enterprise.company import Company

        stmt = stmt.join(Company, JobRequirement.company_id == Company.id).where(Company.slug == company_slug)

    stmt = stmt.order_by(JobRequirement.created_at.desc())

    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/{job_id}", response_model=dict[str, Any])
async def get_public_job(job_id: UUID, session: DBSessionDep) -> dict[str, Any]:
    """Get job details publicly."""
    stmt = (
        select(JobRequirement)
        .options(selectinload(JobRequirement.company), selectinload(JobRequirement.postings))
        .where(JobRequirement.id == job_id)
    )
    result = await session.execute(stmt)
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return {
        "job": JobRequirementResponse.model_validate(job),
        "organization": {
            "name": job.company.name if job.company else "Our Company",
            "logo_url": job.company.logo_url if job.company else None,
            "location": job.company.location if job.company else None,
        },
    }


@router.post("/{job_id}/apply", response_model=dict[str, Any])
async def apply_to_job(
    request: Request, job_id: UUID, session: DBSessionDep, resume: Annotated[UploadFile | None, File()] = None
) -> dict[str, Any]:
    """Allow anyone to apply to a job through a public form with AI analysis."""
    import pypdfium2 as pdfium

    from app.core.ai import analyze_text_with_llm

    # 1. Verify job
    stmt = select(JobRequirement).where(JobRequirement.id == job_id)
    result = await session.execute(stmt)
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # 1.5. Extract all form fields
    try:
        form_data = await request.form()
    except Exception:
        form_data = {}  # type: ignore

    all_fields: dict[str, str] = {}
    final_resume = resume

    if hasattr(form_data, "items"):
        for k, v in form_data.items():
            is_file = hasattr(v, "filename") and hasattr(v, "file")
            if is_file:
                fv = cast("UploadFile", v)
                if not final_resume:
                    if k == "resume" or "resume" in k.lower() or "cv" in k.lower() or "file" in k.lower():
                        final_resume = fv
                all_fields[k] = f"FILE: {fv.filename}"
            else:
                all_fields[k] = str(v)

    # Extract standard fields with fallbacks
    full_name_form = (
        all_fields.get("full_name") or all_fields.get("full_name_") or all_fields.get("name") or ""
    )
    email_form = all_fields.get("email") or all_fields.get("email_address") or ""
    skills_form = all_fields.get("skills") or all_fields.get("key_skills") or ""
    phone_form = all_fields.get("phone") or all_fields.get("phone_number") or ""

    # 2. Process Resume (Extract Text)
    resume_text = ""
    if final_resume:
        try:
            await final_resume.seek(0)
            content = await final_resume.read()

            text_parts = []
            try:
                pdf = pdfium.PdfDocument(content)
                for i in range(len(pdf)):
                    page = pdf.get_page(i)
                    textpage = page.get_textpage()
                    text_parts.append(textpage.get_text_range())
                resume_text = "\n".join(text_parts)
            except Exception:
                with contextlib.suppress(Exception):
                    resume_text = content.decode("utf-8", errors="ignore")
        except Exception:
            traceback.print_exc()

    # 3. AI Analysis
    ai_details: dict[str, Any] = {}
    ai_analysis: dict[str, Any] = {}
    ai_feedback: dict[str, Any] = {}
    ai_score = 0.0

    if resume_text:
        try:
            prompt = f"""
            You are an expert recruiter and ATS system.

            TASK 1: EXTRACT CANDIDATE DETAILS
            Extract the following from the RESUME TEXT:
            - Full Name (if not clearly stated, use 'Candidate')
            - Email
            - Phone Number
            - Current Location (City, Country)
            - Total Years of Experience (Numeric estimate, e.g. 5)
            - List of Key Skills (Technical & Soft)

            TASK 2: FIT ANALYSIS
            Analyze the resume against the JOB DESCRIPTION.
            - Score (0-100): How well do they fit?
            - Fit Reason: Why are they a good fit? (Be specific about matching skills/experience).
            - Not Fit Reason / Gap Analysis: What is missing or weak? (Be specific).
            - Highlights: 3-4 bullet points of their best qualifications.

            JOB TITLE: {job.title}
            JOB DESCRIPTION:
            {job.description}

            RESUME TEXT:
            {resume_text[:12000]}

            OUTPUT JSON FORMAT:
            {{
                "candidate_details": {{
                    "full_name": "...",
                    "email": "...",
                    "phone": "...",
                    "location": "...",
                    "total_experience": 0,
                    "skills": ["..."]
                }},
                "analysis": {{
                    "score": 85,
                    "fit_reason": "...",
                    "not_fit_reason": "...",
                    "highlights": ["..."],
                    "interview_questions": ["..."]
                }}
            }}
            """

            ai_response_str = await analyze_text_with_llm(prompt)
            data_ai = json.loads(ai_response_str)

            ai_details = data_ai.get("candidate_details", {})
            ai_analysis = data_ai.get("analysis", {})

            ai_feedback = ai_analysis
            ai_score = float(ai_analysis.get("score", 0))

            if (
                not full_name_form
                and ai_details.get("full_name")
                and ai_details.get("full_name") != "Candidate"
            ):
                full_name_form = str(ai_details.get("full_name"))
            if not email_form and ai_details.get("email"):
                email_form = str(ai_details.get("email"))
            if not skills_form and ai_details.get("skills"):
                skills_form = ", ".join(cast("list[str]", ai_details.get("skills")))

        except Exception:
            traceback.print_exc()

    if not email_form:
        raise HTTPException(status_code=400, detail="Email could not be extracted from form or resume")

    # 4. Check if candidate exists or create new
    cand_stmt = select(Candidate).where(Candidate.email == email_form)
    cand_res = await session.execute(cand_stmt)
    candidate = cand_res.scalar_one_or_none()

    skills_list = (
        [s.strip() for s in skills_form.split(",")]
        if skills_form
        else cast("list[str]", ai_details.get("skills", []))
    )

    candidate_updates: dict[str, Any] = {
        "full_name": full_name_form or "Candidate",
        "email": email_form,
        "skills": skills_list,
        "phone": phone_form or ai_details.get("phone"),
        "total_experience": ai_details.get("total_experience"),
        "source_platform": all_fields.get("source") or "Careers Page",
        "parsed_data": {"resume_text": resume_text, "form_fields": all_fields, **ai_details, **ai_analysis}
        if (ai_details or all_fields)
        else None,
    }

    if not candidate:
        candidate = Candidate(**candidate_updates, company_id=job.company_id)
        session.add(candidate)
        await session.flush()
    else:
        for k, v in candidate_updates.items():
            if v is not None:
                setattr(candidate, k, v)

        current_parsed = cast("dict[str, Any]", candidate.parsed_data) or {}
        new_parsed = cast("dict[str, Any]", candidate_updates["parsed_data"]) or {}
        current_parsed.update(new_parsed)
        candidate.parsed_data = current_parsed

        session.add(candidate)
        await session.flush()

    # 5. Create Application
    check_app = select(CandidateApplication).where(
        CandidateApplication.candidate_id == candidate.id, CandidateApplication.job_requirement_id == job.id
    )
    res_app = await session.execute(check_app)
    existing_app = res_app.scalar_one_or_none()

    if existing_app:
        return {
            "message": "You have already applied to this position",
            "application_id": str(existing_app.id),
        }

    # Determine original source from MongoDB shortlist if possible
    original_source = all_fields.get("source")
    if not original_source:
        try:
            import os

            from pymongo import MongoClient

            mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
            mongo_db_name = os.getenv("MONGO_DB_NAME", "croar_sourcing")
            client = MongoClient(mongo_uri)
            db = client[mongo_db_name]
            shortlist = db["project_shortlists"].find_one(
                {"job_id": str(job.id), "profile.email": email_form}
            )
            if shortlist and shortlist.get("source"):
                original_source = shortlist.get("source")
        except Exception:
            pass

    if not original_source:
        original_source = "Job Portal"

    application = CandidateApplication(
        candidate_id=candidate.id,
        job_requirement_id=job.id,
        status_id=1,
        current_stage=1,
        source=original_source,
        ai_match_score=ai_score,
        ai_feedback=ai_feedback,
        company_id=job.company_id,
        applied_at=cast("Any", func.now()),
    )
    session.add(application)
    await session.commit()
    await session.refresh(application)

    # 5.1. Update Shortlist Status if applicable
    try:
        import os

        from pymongo import MongoClient

        mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
        mongo_db_name = os.getenv("MONGO_DB_NAME", "croar_sourcing")
        client = MongoClient(mongo_uri)
        db = client[mongo_db_name]
        coll = db["project_shortlists"]

        # Match by job_id and email
        coll.update_one({"job_id": str(job.id), "profile.email": email_form}, {"$set": {"status": "applied"}})
    except Exception as e:
        print(f"Error updating shortlist status: {e}")

    # 6. Trigger Mail Automation (Stage 1 is initial application)
    from app.services.enterprise.automation_service import trigger_automations

    await trigger_automations(application.id, 1, session)

    return {"message": "Application submitted successfully", "application_id": str(application.id)}
