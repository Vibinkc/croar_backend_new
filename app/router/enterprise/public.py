from typing import Annotated, List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, Form, UploadFile, File, Request, BackgroundTasks
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
import os
import json
import traceback

from app.core.dependencies import DBSessionDep
from app.models.enterprise.job import JobRequirement, JobStatus
from app.models.enterprise.candidate import Candidate, CandidateApplication

router = APIRouter(prefix="/public/jobs", tags=["Public Jobs"])

@router.get("/list")
async def list_active_jobs(
    session: DBSessionDep
):
    """Publicly list active jobs."""
    stmt = select(JobRequirement).where(
        JobRequirement.deleted_at == None
    ).order_by(JobRequirement.created_at.desc())
    
    result = await session.execute(stmt)
    return result.scalars().all()

@router.get("/{job_id}")
async def get_public_job(
    job_id: UUID,
    session: DBSessionDep
):
    """Get job details publicly."""
    stmt = select(JobRequirement).options(selectinload(JobRequirement.company)).where(
        JobRequirement.id == job_id
    )
    result = await session.execute(stmt)
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    return {
        "job": job,
        "organization": {
            "name": job.company.name if job.company else "Our Company",
            "logo_url": job.company.logo_url if job.company else None,
            "location": job.company.location if job.company else None
        }
    }

@router.post("/{job_id}/apply")
async def apply_to_job(
    request: Request,
    job_id: UUID,
    resume: Annotated[UploadFile, File()] = None,
    session: DBSessionDep = None
):
    """Allow anyone to apply to a job through a public form with AI analysis."""
    import pypdfium2 as pdfium
    from app.core.ai import analyze_text_with_llm
    
    # 1. Verify job
    stmt = select(JobRequirement).where(JobRequirement.id == job_id)
    result = await session.execute(stmt)
    job = result.scalar_one_or_none()
    if not job: raise HTTPException(status_code=404, detail="Job not found")

    # 1.5. Extract all form fields
    try:
        form_data = await request.form()
    except Exception as e:
        form_data = {}

    all_fields = {}
    final_resume = resume
    
    if hasattr(form_data, 'items'):
        for k, v in form_data.items():
            is_file = hasattr(v, 'filename') and hasattr(v, 'file')
            if is_file:
                if not final_resume:
                    if k == 'resume' or 'resume' in k.lower() or 'cv' in k.lower() or 'file' in k.lower():
                        final_resume = v
                    elif not final_resume: 
                        final_resume = v
                all_fields[k] = f"FILE: {v.filename}"
            else:
                all_fields[k] = str(v)

    # Extract standard fields with fallbacks
    full_name_form = all_fields.get('full_name') or all_fields.get('full_name_') or all_fields.get('name') or ""
    email_form = all_fields.get('email') or all_fields.get('email_address') or ""
    skills_form = all_fields.get('skills') or all_fields.get('key_skills') or ""
    phone_form = all_fields.get('phone') or all_fields.get('phone_number') or ""

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
                try:
                    resume_text = content.decode("utf-8", errors="ignore")
                except Exception:
                    pass
        except Exception as e:
            traceback.print_exc()

    # 3. AI Analysis
    ai_details = {}
    ai_analysis = {}
    ai_feedback = {}
    ai_score = 0
    
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
            data = json.loads(ai_response_str)
            
            ai_details = data.get("candidate_details", {})
            ai_analysis = data.get("analysis", {})
            
            ai_feedback = ai_analysis
            ai_score = ai_analysis.get("score", 0)
            
            if not full_name_form and ai_details.get("full_name") and ai_details.get("full_name") != "Candidate":
                full_name_form = ai_details.get("full_name")
            if not email_form and ai_details.get("email"):
                email_form = ai_details.get("email")
            if not skills_form and ai_details.get("skills"):
                 skills_form = ", ".join(ai_details.get("skills"))
            
        except Exception as e:
            traceback.print_exc()

    if not email_form:
        raise HTTPException(status_code=400, detail="Email could not be extracted from form or resume")

    # 4. Check if candidate exists or create new
    stmt = select(Candidate).where(Candidate.email == email_form)
    result = await session.execute(stmt)
    candidate = result.scalar_one_or_none()
    
    skills_list = [s.strip() for s in skills_form.split(",")] if skills_form else ai_details.get("skills", [])
    
    candidate_updates = {
        "full_name": full_name_form or "Candidate",
        "email": email_form,
        "skills": skills_list,
        "phone": phone_form or ai_details.get("phone"),
        "total_experience": ai_details.get("total_experience"),
        "source_platform": all_fields.get("source") or "Careers Page",
        "parsed_data": {
            "resume_text": resume_text, 
            "form_fields": all_fields,
            **ai_details, 
            **ai_analysis
        } if (ai_details or all_fields) else None
    }

    if not candidate:
        candidate = Candidate(
            **candidate_updates
        )
        session.add(candidate)
        await session.flush()
    else:
        for k, v in candidate_updates.items():
            if v is not None:
                setattr(candidate, k, v)
        if candidate.parsed_data:
            candidate.parsed_data.update(candidate_updates["parsed_data"] or {})
        else:
            candidate.parsed_data = candidate_updates["parsed_data"]
        
        session.add(candidate)
        await session.flush()

    # 5. Create Application
    check_app = select(CandidateApplication).where(
        CandidateApplication.candidate_id == candidate.id,
        CandidateApplication.job_requirement_id == job.id
    )
    res_app = await session.execute(check_app)
    existing_app = res_app.scalar_one_or_none()

    if existing_app:
        return {"message": "You have already applied to this position", "application_id": existing_app.id}

    application = CandidateApplication(
        candidate_id=candidate.id,
        job_requirement_id=job.id,
        status_id=1,
        current_stage=1, 
        ai_match_score=ai_score,
        ai_feedback=ai_feedback,
        applied_at=func.now()
    )
    session.add(application)
    await session.commit()
    await session.refresh(application)

    # 6. Trigger Mail Automation (Stage 1 is initial application)
    from app.services.enterprise.automation_service import trigger_automations
    from fastapi import BackgroundTasks
    
    # We can't easily get BackgroundTasks here since it's not in the path params,
    # but we can import it or just let the service handle it (it already has a fallback)
    await trigger_automations(application.id, 1, session)
    
    return {"message": "Application submitted successfully", "application_id": application.id}
