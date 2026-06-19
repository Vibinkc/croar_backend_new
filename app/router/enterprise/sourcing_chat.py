import os
import re
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from pymongo import MongoClient
from sqlalchemy import select

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.job import JobRequirement
from app.models.shared.constants import ModuleScope, PermissionAction

router = APIRouter(prefix="/sourcing/chat", tags=["Sourcing Chat"])

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "croar_sourcing")

# A single shared client reuses one connection pool for the whole process. Previously a
# new MongoClient (with its own pool + monitor threads) was created on every request,
# which leaks threads and exhausts connections under load.
_mongo_client = MongoClient(MONGO_URI)


def _db():
    return _mongo_client[MONGO_DB_NAME]


class ChatMessage(BaseModel):
    role: str
    content: str
    timestamp: str | None = None
    results: list[dict[str, Any]] | None = None


class ChatSession(BaseModel):
    session_id: str | None = None
    title: str
    messages: list[ChatMessage]
    created_at: str | None = None
    updated_at: str | None = None


@router.post("/sessions")
async def save_chat_session(
    session: ChatSession,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    """Save or update a chat session in the sourcing_chat_history collection, tagged by company."""
    db = _db()
    coll = db["sourcing_chat_history"]

    company_id = str(getattr(current_user, "company_id", ""))
    session_data = session.dict()
    session_data["company_id"] = company_id

    session_id = session_data.get("session_id")
    if not session_id:
        session_id = str(uuid.uuid4())
        session_data["session_id"] = session_id

    session_data["updated_at"] = datetime.now().isoformat()
    if not session_data.get("created_at"):
        session_data["created_at"] = datetime.now().isoformat()

    coll.update_one({"session_id": session_id}, {"$set": session_data}, upsert=True)
    return {"status": "success", "session_id": session_id}


@router.get("/sessions")
async def list_chat_sessions(
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    """List all saved chat sessions for the current company."""
    db = _db()
    coll = db["sourcing_chat_history"]

    company_id = str(getattr(current_user, "company_id", ""))
    sessions = list(
        coll.find({"company_id": company_id}, {"_id": 0, "messages": 0}).sort("updated_at", -1).limit(200)
    )
    return sessions


@router.get("/sessions/{session_id}")
async def get_chat_session(
    session_id: str,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    """Retrieve a specific chat session, verifying company ownership."""
    db = _db()
    coll = db["sourcing_chat_history"]

    company_id = str(getattr(current_user, "company_id", ""))
    session = coll.find_one({"session_id": session_id, "company_id": company_id}, {"_id": 0})
    if not session:
        return {"error": "Session not found or access denied"}
    return session


@router.delete("/sessions/{session_id}")
async def delete_chat_session(
    session_id: str,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    """Delete a chat session (scoped to the caller's company)."""
    db = _db()
    coll = db["sourcing_chat_history"]

    company_id = str(getattr(current_user, "company_id", ""))
    coll.delete_one({"session_id": session_id, "company_id": company_id})
    return {"status": "deleted"}


# --- JOB SHORTLISTING ---


@router.get("/jobs")
async def list_available_jobs(
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    """Fetch existing job requirements from SQL to show in the shortlist modal, filtered by company."""
    company_id = getattr(current_user, "company_id", None)
    stmt = select(JobRequirement.id, JobRequirement.title).where(JobRequirement.company_id == company_id)
    res = await db.execute(stmt)
    jobs = [{"id": str(row[0]), "title": row[1]} for row in res.all()]
    return jobs


@router.post("/shortlist")
async def shortlist_candidate(
    data: dict[str, Any],
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    """Save a candidate profile to a job in MongoDB."""
    db = _db()
    coll = db["project_shortlists"]  # Keeping collection name or renaming to job_shortlists

    company_id = str(getattr(current_user, "company_id", ""))

    profile = data.get("profile")
    if not isinstance(profile, dict):
        raise HTTPException(status_code=400, detail="A candidate 'profile' object is required")

    shortlist_entry = {
        "shortlist_id": str(uuid.uuid4()),
        "job_id": data.get("job_id"),
        "job_title": data.get("job_title"),
        "profile": profile,
        "source": data.get("source", "AI Sourcing"),
        "company_id": company_id,
        "shortlisted_at": datetime.now().isoformat(),
        "status": "Shortlisted",
    }

    # Avoid duplicate shortlists for same profile in same job for same company
    profile_url = profile.get("profile_url")
    job_id = shortlist_entry["job_id"]

    coll.update_one(
        {"profile.profile_url": profile_url, "job_id": job_id, "company_id": company_id},
        {"$set": shortlist_entry},
        upsert=True,
    )
    return {
        "status": "success",
        "shortlist_id": shortlist_entry["shortlist_id"],
        "source": shortlist_entry["source"],
    }


@router.get("/shortlisted")
async def list_shortlisted_candidates(
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
    job_id: str | None = None,
):
    """List all shortlisted candidates, filtered by company and optionally by job."""
    db = _db()
    coll = db["project_shortlists"]

    company_id = str(getattr(current_user, "company_id", ""))

    query = {"company_id": company_id}
    if job_id:
        # Allow only id-shaped values: a legitimate job_id (UUID) always matches, while anything
        # carrying Mongo operators/structure cannot, so no user input can shape the query.
        safe_job_id = str(job_id)
        query["job_id"] = safe_job_id if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", safe_job_id) else "\x00"

    shortlists = list(coll.find(query, {"_id": 0}).sort("shortlisted_at", -1).limit(500))
    return shortlists


@router.delete("/shortlisted/{shortlist_id}")
async def remove_shortlisted_candidate(
    shortlist_id: str,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    """Remove a candidate from the shortlist (scoped to the caller's company)."""
    db = _db()
    coll = db["project_shortlists"]

    company_id = str(getattr(current_user, "company_id", ""))
    coll.delete_one({"shortlist_id": shortlist_id, "company_id": company_id})
    return {"status": "deleted"}


@router.post("/shortlisted/{shortlist_id}/move")
async def move_shortlisted_candidate(
    shortlist_id: str,
    data: dict,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.update))
    ],
):
    """Move a shortlisted candidate to a different job requirement."""
    db = _db()
    coll = db["project_shortlists"]

    target_job_id = data.get("job_id")
    target_job_title = data.get("job_title")

    if not target_job_id or not target_job_title:
        return {"error": "Target job details required"}

    # Update the shortlist entry
    result = coll.update_one(
        {"shortlist_id": shortlist_id}, {"$set": {"job_id": target_job_id, "job_title": target_job_title}}
    )

    if result.matched_count == 0:
        return {"error": "Shortlist entry not found"}

    return {"status": "success", "message": f"Moved to {target_job_title}"}


@router.get("/engagement/{shortlist_id}")
async def get_engagement_details(shortlist_id: str):
    """Fetch job and candidate details for the engagement form (Public)."""
    db = _db()
    coll = db["project_shortlists"]

    shortlist = coll.find_one({"shortlist_id": shortlist_id}, {"_id": 0})
    if not shortlist:
        return {"error": "Engagement not found"}

    return shortlist


class CandidateInterestRequest(BaseModel):
    previous_company: str
    current_salary: str
    expected_salary: str
    notice_period: str
    total_experience: str
    relevant_experience: str
    work_preference: str  # Remote, Hybrid, On-site
    top_skills: str
    reason_for_change: str | None = None
    other_details: dict[str, Any] | None = None


@router.post("/engagement/{shortlist_id}/interest")
async def save_candidate_interest(shortlist_id: str, data: CandidateInterestRequest):
    """Save candidate basic info to a new master collection and update shortlist status."""
    db = _db()

    # 1. Update the specific Shortlist entry
    shortlist_coll = db["project_shortlists"]
    shortlist = shortlist_coll.find_one({"shortlist_id": shortlist_id})

    if not shortlist:
        return {"error": "Shortlist entry not found"}

    interest_data = data.dict()
    interest_data["interest_filled_at"] = datetime.now().isoformat()

    shortlist_coll.update_one(
        {"shortlist_id": shortlist_id},
        {"$set": {"candidate_interest": interest_data, "status": "Interest Expressed"}},
    )

    # 2. Save/Update to the NEW Master Collection: candidate_engagement_data
    # We use the email as the unique identifier for the candidate's master profile
    engagement_coll = db["candidate_engagement_data"]
    candidate_email = shortlist.get("profile", {}).get("email")

    if candidate_email:
        master_data = {
            "email": candidate_email,
            "full_name": shortlist.get("profile", {}).get("full_name"),
            "basic_info": interest_data,
            "last_updated": datetime.now().isoformat(),
            "profile_url": shortlist.get("profile", {}).get("profile_url"),
        }

        engagement_coll.update_one({"email": candidate_email}, {"$set": master_data}, upsert=True)

    return {"status": "success", "message": "Interest saved and profile enriched"}


class SendJDRequest(BaseModel):
    email: str
    full_name: str
    job_title: str
    job_id: str
    profile_url: str | None = None


@router.post("/send-jd")
async def send_job_description(
    request: SendJDRequest,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
    session: DBSessionDep,
):
    """Send a Job Description email to a candidate with an application link."""
    from app.models.enterprise.company import Company

    from .communication import send_smtp_email

    company_id = getattr(current_user, "company_id", None)
    stmt = select(Company).where(Company.id == company_id).limit(1)
    res = await session.execute(stmt)
    company = res.scalar_one_or_none()

    company_name = company.name if company else "Our Company"
    company_logo = company.logo_url if company else None

    from app.core.settings import settings

    # Generate public engagement link
    # Find the shortlist entry to get the shortlist_id
    db = _db()
    coll = db["project_shortlists"]

    shortlist = coll.find_one({"job_id": request.job_id, "profile.email": request.email}, {"shortlist_id": 1})
    shortlist_id = shortlist.get("shortlist_id") if shortlist else "unknown"

    app_link = f"{settings.frontend_url}/engagement/{shortlist_id}"
    print(f"DEBUG: Generated engagement link: {app_link}")

    subject = f"Opportunity: {request.job_title} at {company_name}"

    body = f"""
    <p>Hello {request.full_name},</p>

    <p>I hope this email finds you well.</p>

    <p>We've been following your impressive professional background and believe your skills would be a fantastic match for the <strong>{request.job_title}</strong> position at <strong>{company_name}</strong>.</p>

    <p>We are currently looking for talented individuals to join our team, and we'd love for you to review the role and consider exploring this opportunity with us.</p>

    <div style="margin: 30px 0; padding: 25px; background-color: #f8fafc; border-radius: 16px; border: 1px solid #e2e8f0; text-align: center;">
        <h3 style="margin-top: 0; color: #1e293b; font-size: 18px;">{request.job_title}</h3>
        <p style="color: #64748b; margin-bottom: 20px;">Review the full job description and share your details with us below:</p>

        <a href="{app_link}" style="display: inline-block; padding: 14px 28px; background-color: #7C3AED; color: #ffffff; text-decoration: none; border-radius: 12px; font-weight: bold; font-size: 14px; box-shadow: 0 4px 6px -1px rgba(124, 58, 237, 0.2);">View Job & Apply Now</a>

        {f"<p style='margin-top: 20px; font-size: 11px; color: #94a3b8;'>Reference Profile: <a href='{request.profile_url}' style='color: #7C3AED;'>View Profile</a></p>" if request.profile_url else ""}
    </div>

    <p>If you have any questions before applying, feel free to reply to this email directly.</p>

    <p>Best regards,<br>
    <strong>The Recruiting Team</strong><br>
    {company_name}</p>
    """

    success, error = send_smtp_email(
        to_email=request.email, subject=subject, body=body, company_name=company_name, logo_url=company_logo
    )

    if not success:
        raise HTTPException(status_code=500, detail=f"Failed to send email: {error}")

    # Update status in MongoDB
    db = _db()
    coll = db["project_shortlists"]

    coll.update_one(
        {"job_id": request.job_id, "profile.email": request.email}, {"$set": {"status": "mail_sent"}}
    )

    return {"status": "success", "message": f"JD sent to {request.email}"}
