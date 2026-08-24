import base64
import os
import re
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
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


# Ids we mint are uuid4 hex and company ids are UUID strings; the url-safe charset below
# covers both while keeping legacy values working. Anything else can only be an attempt to
# push a non-string (a Mongo operator document) into a query filter, so it never gets there.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _clean_id(value: Any) -> str:
    """Coerce an identifier to a charset-checked string for use in a query filter.

    Returns "" for anything malformed, which yields an empty result set - the same outcome
    an unknown id already produced - instead of letting the value steer the query.
    """
    text = value if isinstance(value, str) else ""
    return text if _SAFE_ID.match(text) else ""


def _require_id(value: Any, field: str) -> str:
    """Same check for ids that arrive straight off the URL, where a bad one is worth a 400."""
    cleaned = _clean_id(value)
    if not cleaned:
        raise HTTPException(status_code=400, detail=f"Invalid {field}.")
    return cleaned


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
    # The original query text (subtitle shown in the New Search dropdown). Optional for older sessions.
    query: str | None = None
    messages: list[ChatMessage]
    # The job this search was locked to (when launched from the Pipeline / a job page). Persisted so
    # reopening the session from History re-locks the shortlist to that job. None for a free search.
    job_id: str | None = None
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


class ShareSearchBody(BaseModel):
    query: str
    title: str | None = None
    criteria: list[str] = []
    profiles: list[dict[str, Any]] = []


@router.post("/share")
async def create_shared_search(
    body: ShareSearchBody,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    """Create a PUBLIC, read-only snapshot of a search (up to 30 profiles) that anyone with the link
    can view — no login required. Heavy scrape data is stripped before storing."""
    coll = _db()["shared_searches"]
    company_id = str(getattr(current_user, "company_id", ""))

    def _slim(p: dict[str, Any]) -> dict[str, Any]:
        p = dict(p or {})
        p.pop("raw_data", None)
        p.pop("html", None)
        return p

    share_id = uuid.uuid4().hex
    coll.insert_one(
        {
            "share_id": share_id,
            "company_id": company_id,
            "query": body.query,
            "title": body.title or body.query,
            "criteria": body.criteria or [],
            "profiles": [_slim(p) for p in (body.profiles or [])[:30]],
            "created_at": datetime.now().isoformat(),
        }
    )
    return {"share_id": share_id}


@router.get("/share/{share_id}")
async def get_shared_search(share_id: str):
    """PUBLIC (no auth): fetch a shared search snapshot for the public viewer page."""
    if not re.fullmatch(r"[A-Za-z0-9]{8,64}", share_id or ""):
        raise HTTPException(status_code=404, detail="Not found")
    doc = _db()["shared_searches"].find_one({"share_id": share_id}, {"_id": 0, "company_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="This shared search doesn't exist or was removed.")
    return {**doc, "brand": "Croar"}


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

    owner = getattr(current_user, "first_name", None) or getattr(current_user, "email", None) or "—"
    shortlist_entry = {
        "shortlist_id": str(uuid.uuid4()),
        "job_id": data.get("job_id"),
        "job_title": data.get("job_title"),
        "project_id": data.get("project_id"),  # ties agent-shortlisted candidates to their project
        "profile": profile,
        "source": data.get("source", "AI Sourcing"),
        "company_id": company_id,
        "owner": owner,  # who shortlisted the candidate (shown in the Shortlist table)
        "shortlisted_at": datetime.now().isoformat(),
        "status": data.get("status") or "Not Contacted",
    }

    # Avoid duplicate shortlists for same profile in same job for same company
    profile_url = profile.get("profile_url")
    job_id = shortlist_entry["job_id"]

    coll.update_one(
        {"profile.profile_url": profile_url, "job_id": job_id, "company_id": company_id},
        {"$set": shortlist_entry},
        upsert=True,
    )

    # Also surface this candidate on the JOB's "Profile Sourcing" tab (job detail page), scoped to
    # the job it was shortlisted for — so sourced/shortlisted candidates flow into that job's funnel.
    if job_id:
        try:
            from fastapi.concurrency import run_in_threadpool

            from app.services.enterprise.sourcing import job_sourcing

            await run_in_threadpool(job_sourcing.record_shortlist, str(job_id), company_id, profile)
        except Exception:
            pass

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


@router.get("/projects/{project_id}/candidates")
async def list_project_candidates(
    project_id: str,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    """All candidates tied to a project (AI-sourced, manually approved, or directly shortlisted),
    for the in-project Candidates tab where the user reviews & verifies."""
    company_id = _clean_id(str(getattr(current_user, "company_id", "")))
    safe_project_id = _require_id(project_id, "project_id")
    rows = list(
        _db()["project_shortlists"]
        .find({"company_id": company_id, "project_id": safe_project_id}, {"_id": 0})
        .sort("shortlisted_at", -1)
        .limit(500)
    )
    return {"candidates": rows, "total": len(rows)}


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


class ShortlistStatusBody(BaseModel):
    status: str


@router.post("/shortlisted/{shortlist_id}/status")
async def update_shortlist_status(
    shortlist_id: str,
    body: ShortlistStatusBody,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    """Update the outreach status of a shortlisted candidate (Not Contacted / Contacted / …)."""
    company_id = str(getattr(current_user, "company_id", ""))
    allowed = {"Not Contacted", "Contacted", "Responded", "Interested", "Not a fit", "Hired", "No Response"}
    status = body.status if body.status in allowed else "Not Contacted"
    res = _db()["project_shortlists"].update_one(
        {"shortlist_id": shortlist_id, "company_id": company_id}, {"$set": {"status": status}}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Shortlisted candidate not found.")
    return {"status": "success", "new_status": status}


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


# ---------------------------------------------------------------------------
# Talent Search PROJECTS + AGENT automation (Juicebox-style).
# A project wraps a saved search + an autonomous "agent" config (daily target,
# outreach mode, approval type). Stored in Mongo so no migration is needed.
# ---------------------------------------------------------------------------


def _projects():
    return _db()["sourcing_projects"]


class ProjectBody(BaseModel):
    name: str
    department: str | None = None
    visibility: str | None = "Shared"


class AgentSettingsBody(BaseModel):
    status: str | None = None  # calibrating | sourcing | paused | none
    paused: bool | None = None  # deactivate the agent without losing its status
    daily_target: int | None = None  # profiles/day: 5,15,25,50,75
    outreach_mode: str | None = None  # ai_sequence | existing | shortlist
    approval_type: str | None = None  # automatic | manual
    filters: list[dict[str, Any]] | None = None
    criteria: list[str] | None = None
    collaborators: list[str] | None = None
    query: str | None = None
    department: str | None = None
    visibility: str | None = None
    stats: dict[str, int] | None = None
    ats_job_id: str | None = None
    ats_job_title: str | None = None
    outreach_sequence_id: str | None = None
    outreach_sequence_name: str | None = None
    # Response SLA: days a contacted candidate has to reply before we re-source a
    # replacement. 0 / None = disabled (never auto re-source).
    response_window_days: int | None = None
    auto_resource: bool | None = None  # when overdue, automatically source replacements


def _project_public(doc: dict[str, Any]) -> dict[str, Any]:
    doc.pop("_id", None)
    doc.setdefault("agent", {})
    return doc


def _project_stats(company_id: str, project_id: str) -> dict[str, int]:
    """Live counts from the shortlist rows tagged with this project."""
    rows = list(
        _db()["project_shortlists"].find(
            {"company_id": _clean_id(company_id), "project_id": _clean_id(project_id)},
            {"status": 1, "_id": 0},
        )
    )
    contacted_states = {"Contacted", "mail_sent", "Responded", "Interested", "Interest Expressed", "Hired"}
    interested_states = {"Interested", "Responded", "Interest Expressed", "Hired"}
    return {
        "shortlisted": len(rows),
        "contacted": sum(1 for r in rows if (r.get("status") or "") in contacted_states),
        "interested": sum(1 for r in rows if (r.get("status") or "") in interested_states),
    }


@router.get("/projects")
async def list_projects(
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    """All Talent-Search projects for the company (newest first)."""
    company_id = str(getattr(current_user, "company_id", ""))
    rows = list(_projects().find({"company_id": company_id}, {"_id": 0}).sort("created_at", -1).limit(200))
    for r in rows:
        r["stats"] = _project_stats(company_id, r.get("project_id", ""))
    return rows


@router.post("/projects")
async def create_project(
    body: ProjectBody,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    """Create a new project (a workspace for a search + optional automation agent)."""
    company_id = str(getattr(current_user, "company_id", ""))
    owner = getattr(current_user, "first_name", None) or getattr(current_user, "email", None) or "—"
    project_id = uuid.uuid4().hex
    doc = {
        "project_id": project_id,
        "company_id": company_id,
        "name": (body.name or "Untitled project").strip(),
        "owner": owner,
        "department": body.department,
        "visibility": body.visibility or "Shared",
        "collaborators": [],
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        # Agent config — "none" until the user configures it.
        "agent": {
            "status": "none",  # none | calibrating | sourcing | paused
            "daily_target": 15,
            "outreach_mode": "shortlist",
            "approval_type": "manual",
            "filters": [],
            "criteria": [],
            "query": "",
        },
        "stats": {"shortlisted": 0, "contacted": 0, "interested": 0},
    }
    _projects().insert_one(doc)
    return _project_public(doc)


@router.get("/projects/{project_id}")
async def get_project(
    project_id: str,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    company_id = str(getattr(current_user, "company_id", ""))
    doc = _projects().find_one({"project_id": project_id, "company_id": company_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Project not found.")
    doc["stats"] = _project_stats(company_id, project_id)
    return doc


@router.patch("/projects/{project_id}")
async def update_project(
    project_id: str,
    body: AgentSettingsBody,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    """Update the project's agent config / metadata (only provided fields change)."""
    company_id = str(getattr(current_user, "company_id", ""))
    doc = _projects().find_one({"project_id": project_id, "company_id": company_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Project not found.")

    agent = doc.get("agent", {}) or {}
    for f in (
        "status",
        "paused",
        "daily_target",
        "outreach_mode",
        "approval_type",
        "filters",
        "criteria",
        "query",
        "outreach_sequence_id",
        "outreach_sequence_name",
        "response_window_days",
        "auto_resource",
    ):
        v = getattr(body, f)
        if v is not None:
            agent[f] = v
    set_doc: dict[str, Any] = {"agent": agent, "updated_at": datetime.now().isoformat()}
    if body.collaborators is not None:
        set_doc["collaborators"] = body.collaborators
    if body.department is not None:
        set_doc["department"] = body.department
    if body.visibility is not None:
        set_doc["visibility"] = body.visibility
    if body.ats_job_id is not None:
        set_doc["ats_job_id"] = body.ats_job_id or None
    if body.ats_job_title is not None:
        set_doc["ats_job_title"] = body.ats_job_title or None
    if body.stats is not None:
        stats = doc.get("stats", {}) or {}
        stats.update(body.stats)
        set_doc["stats"] = stats
    _projects().update_one({"project_id": project_id, "company_id": company_id}, {"$set": set_doc})
    updated = _projects().find_one({"project_id": project_id, "company_id": company_id}, {"_id": 0})
    if updated:
        updated["stats"] = _project_stats(company_id, project_id)
    return updated


@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: str,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.update))
    ],
):
    company_id = str(getattr(current_user, "company_id", ""))
    _projects().delete_one({"project_id": project_id, "company_id": company_id})
    return {"status": "deleted"}


class ProjectContactBody(BaseModel):
    emails: list[str] = []
    subject: str | None = None
    message: str | None = None


@router.post("/projects/{project_id}/contact")
async def contact_project_candidates(
    project_id: str,
    body: ProjectContactBody,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    """Agent outreach: email the given candidates and mark them 'Contacted' on this project's
    shortlist. While testing, mail is redirected to the test inbox (PILOT_TEST_MODE)."""
    from app.router.agents import PILOT_TEST_EMAIL, PILOT_TEST_MODE

    from .communication import send_company_email

    company_id = str(getattr(current_user, "company_id", ""))
    coll = _db()["project_shortlists"]
    subject_base = body.subject or "We'd love to connect about an opportunity"
    html = body.message or (
        "<p>Hi,</p><p>We came across your profile and think you could be a great fit for a role "
        "we're hiring for. We'd love to connect and share more.</p><p>Best regards,<br/>Hiring Team</p>"
    )
    sent = 0
    for raw in body.emails:
        email = (raw or "").strip()
        if not email:
            continue
        recipient = PILOT_TEST_EMAIL if PILOT_TEST_MODE else email
        subject = ("[TEST] " if PILOT_TEST_MODE else "") + subject_base
        try:
            ok, _err = await send_company_email(company_id, recipient, subject, html)
        except Exception:
            ok = False
        if ok:
            # Stamp contacted_at + store the message we sent (for the conversation thread), and
            # clear any stale response markers from a previous outreach round.
            coll.update_one(
                {"project_id": project_id, "company_id": company_id, "profile.email": email},
                {
                    "$set": {
                        "status": "Contacted",
                        "contacted_at": datetime.now().isoformat(),
                        "sent_subject": subject_base,
                        "sent_body": html,
                        "sent_at": datetime.now().isoformat(),
                    },
                    "$unset": {
                        "responded_at": "",
                        "no_response_at": "",
                        "reply_subject": "",
                        "reply_body": "",
                        "reply_at": "",
                        "reply_from": "",
                    },
                },
            )
            sent += 1
    return {"sent": sent, "test_mode": PILOT_TEST_MODE}


@router.post("/projects/{project_id}/check-responses")
async def check_responses(
    project_id: str,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    """Response-window SLA sweep for a project.

    Any candidate still 'Contacted' who hasn't replied within the project's
    `response_window_days` is flagged 'No Response', and we report how many replacements
    should be sourced. Runs ON-DEMAND (called when the project opens / on refresh) because
    there is no background scheduler yet — so the SLA is evaluated whenever someone looks.
    """
    from datetime import timedelta

    from app.models.enterprise.communication import EmailLog

    company_uuid = getattr(current_user, "company_id", None)
    company_id = _clean_id(str(company_uuid or ""))
    safe_project_id = _require_id(project_id, "project_id")
    proj = _projects().find_one(
        {"project_id": safe_project_id, "company_id": company_id}, {"_id": 0, "agent": 1}
    )
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found.")

    agent = proj.get("agent", {}) or {}
    window = int(agent.get("response_window_days") or 0)
    cutoff = datetime.now() - timedelta(days=window) if window > 0 else None
    coll = _db()["project_shortlists"]

    contacted_docs = list(
        coll.find({"project_id": safe_project_id, "company_id": company_id, "status": "Contacted"})
    )

    # Index the latest INBOUND reply per sender for this company (from the synced inbox), so a
    # candidate who replied is marked 'Responded' — and their reply is stored on the candidate
    # for the admin to read — BEFORE the overdue sweep ever flags them 'No Response'.
    replies: dict[str, Any] = {}
    if contacted_docs and company_uuid is not None:
        stmt = (
            select(EmailLog)
            .where(EmailLog.direction == "INBOUND", EmailLog.company_id == company_uuid)
            .order_by(EmailLog.sent_at.desc())
        )
        for row in (await session.execute(stmt)).scalars().all():
            key = (row.sender_email or "").strip().lower()
            if key and key not in replies:
                replies[key] = row

    def _naive(dt: Any) -> Any:
        return dt.replace(tzinfo=None) if getattr(dt, "tzinfo", None) else dt

    responded = 0
    overdue = 0
    for doc in contacted_docs:
        ca = doc.get("contacted_at")
        try:
            contacted = datetime.fromisoformat(str(ca)) if ca else None
        except Exception:
            contacted = None

        email = ((doc.get("profile") or {}).get("email") or "").strip().lower()
        reply = replies.get(email) if email else None
        replied = bool(
            reply is not None
            and reply.sent_at is not None
            and (contacted is None or _naive(reply.sent_at) >= contacted)
        )

        if replied and reply is not None:
            coll.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "status": "Responded",
                        "responded_at": datetime.now().isoformat(),
                        "reply_subject": reply.subject,
                        "reply_body": (reply.body or "")[:8000],
                        "reply_from": reply.sender_email,
                        "reply_at": reply.sent_at.isoformat() if reply.sent_at else None,
                    }
                },
            )
            responded += 1
        elif cutoff is not None and contacted is not None and contacted <= cutoff:
            coll.update_one(
                {"_id": doc["_id"]},
                {"$set": {"status": "No Response", "no_response_at": datetime.now().isoformat()}},
            )
            overdue += 1

    return {
        "enabled": window > 0,
        "responded": responded,
        "overdue": overdue,
        "window_days": window,
        "needs_resourcing": overdue,
        "auto_resource": bool(agent.get("auto_resource")),
    }


class CalibrateBody(BaseModel):
    mode: str  # start | approve | message
    query: str = ""
    criteria: list[str] = []
    total: int | None = None
    profile: dict[str, Any] | None = None
    reason: str | None = None
    message: str | None = None


def _profile_brief(p: dict[str, Any] | None) -> str:
    if not p:
        return ""
    parts = [
        p.get("full_name") or "Candidate",
        f"— {p.get('headline')}" if p.get("headline") else "",
        f"at {p.get('company')}" if p.get("company") else "",
        f"in {p.get('location')}" if p.get("location") else "",
    ]
    skills = ", ".join((p.get("skills") or [])[:8])
    if skills:
        parts.append(f"| skills: {skills}")
    if p.get("ai_summary"):
        parts.append(f"| {p['ai_summary'][:400]}")
    return " ".join(x for x in parts if x)


@router.post("/projects/{project_id}/calibrate")
async def calibrate_chat(
    project_id: str,
    body: CalibrateBody,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    """Conversational calibration: the agent talks like a sourcing teammate, and LEARNS from the
    recruiter's approvals — extracting semantic criteria to bias the search toward similar profiles.
    Returns {message, add_criteria}. The frontend re-runs/re-ranks the search with the new criteria."""
    import json as _json

    from app.core.ai import client

    crit = ", ".join(body.criteria or []) or "none yet"
    if body.mode == "start":
        instruction = (
            f'The recruiter is looking for: "{body.query}". You just ran the search and found '
            f"{body.total or 'several'} matches. In 1-2 warm, concise sentences (like a sourcing "
            f"teammate), say you built the search and are surfacing top picks for review. add_criteria: []."
        )
    elif body.mode == "approve":
        instruction = (
            f'The recruiter APPROVED this candidate for the search "{body.query}":\n'
            f'{_profile_brief(body.profile)}\nApproval reason: "{body.reason or "good fit"}".\n'
            f"Current ranking criteria: {crit}.\n"
            f"In 1-2 sentences, say you'll capture what worked about this profile and bias the search "
            f"toward more like it. Then extract 2-4 SHORT semantic criteria (2-4 words each: skills, "
            f"seniority, or traits) that describe why this profile fits, to add to the ranking. "
            f"Do NOT repeat criteria already present. Return add_criteria as a list of short strings."
        )
    else:  # message / free-text feedback
        instruction = (
            f'The recruiter said: "{body.message}". The current search is "{body.query}" with criteria: '
            f"{crit}. Reply helpfully in 1-2 sentences as a sourcing teammate, and extract any NEW short "
            f"ranking criteria implied by their message (else empty). Return add_criteria as short strings."
        )

    prompt = (
        "You are Croar's AI sourcing agent, calibrating a candidate search with a recruiter. "
        'Always reply as JSON: {"message": string, "add_criteria": string[]}. '
        "Keep message short, natural, and specific. Never invent candidate facts.\n\n" + instruction
    )
    try:
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You output only JSON with keys message and add_criteria."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.5,
        )
        data = _json.loads(resp.choices[0].message.content or "{}")
        message = str(data.get("message") or "").strip()
        add = [str(c).strip() for c in (data.get("add_criteria") or []) if str(c).strip()][:4]
    except Exception:
        message = (
            "Got it — I'll factor that into the search."
            if body.mode != "start"
            else "I've built this search — here are the top picks."
        )
        add = []

    return {"message": message, "add_criteria": add}


class AnalyzeProfileBody(BaseModel):
    profile: dict[str, Any]
    criteria: list[str] = []


@router.post("/projects/{project_id}/analyze-profile")
async def analyze_profile(
    project_id: str,
    body: AnalyzeProfileBody,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    """Grade a candidate against each ranking criterion, returning a verdict + a grounded 1-sentence
    explanation per criterion — for the Review Profiles panel. Never invents facts."""
    import json as _json

    from app.core.ai import client

    if not body.criteria:
        return {"analysis": []}
    crit_lines = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(body.criteria))
    prompt = (
        "You are evaluating a sourced candidate against a recruiter's ranking criteria. "
        "Using ONLY the candidate data provided (never invent facts), grade EACH criterion.\n\n"
        f"CANDIDATE:\n{_profile_brief(body.profile)}\n\n"
        f"CRITERIA:\n{crit_lines}\n\n"
        'Return JSON: {"analysis":[{"criterion": <the criterion text>, "verdict": "Good Match"|'
        '"Partial Match"|"No Signal", "explanation": <one sentence, grounded in the candidate data>}]}. '
        "One entry per criterion, in order."
    )
    try:
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You output only JSON with an analysis array."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        data = _json.loads(resp.choices[0].message.content or "{}")
        analysis = data.get("analysis") or []
    except Exception:
        analysis = [
            {
                "criterion": c,
                "verdict": "No Signal",
                "explanation": "Couldn't analyze this criterion right now.",
            }
            for c in body.criteria
        ]
    return {"analysis": analysis}


# ---------------------------------------------------------------------------
# Outreach SEQUENCES (multi-step email campaigns) — Juicebox-style.
# Stored in Mongo (schemaless, no migration). Real scheduled multi-step SENDING and
# open/click tracking are a later phase; this covers authoring + AI generation.
# ---------------------------------------------------------------------------


def _sequences():
    return _db()["sequences"]


class SequenceBody(BaseModel):
    name: str
    steps: list[dict[str, Any]] = []
    privacy: str | None = "Shared"


def _seq_public(doc: dict[str, Any]) -> dict[str, Any]:
    doc.pop("_id", None)
    doc.setdefault("steps", [])
    doc.setdefault(
        "stats",
        {"total": 0, "active": 0, "opened": 0, "clicked": 0, "replied": 0, "interested": 0, "bounced": 0},
    )
    return doc


@router.get("/sequences")
async def list_sequences(
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    company_id = str(getattr(current_user, "company_id", ""))
    rows = list(_sequences().find({"company_id": company_id}, {"_id": 0}).sort("created_at", -1).limit(200))
    return [_seq_public(r) for r in rows]


@router.post("/sequences")
async def create_sequence(
    body: SequenceBody,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    company_id = str(getattr(current_user, "company_id", ""))
    owner = getattr(current_user, "first_name", None) or getattr(current_user, "email", None) or "—"
    seq_id = uuid.uuid4().hex
    doc = {
        "sequence_id": seq_id,
        "company_id": company_id,
        "name": (body.name or "Untitled sequence").strip(),
        "owner": owner,
        "privacy": body.privacy or "Shared",
        "steps": body.steps or [],
        "stats": {
            "total": 0,
            "active": 0,
            "opened": 0,
            "clicked": 0,
            "replied": 0,
            "interested": 0,
            "bounced": 0,
        },
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }
    _sequences().insert_one(doc)
    return _seq_public(doc)


# NOTE: this MUST be declared before "/sequences/{sequence_id}" — otherwise the literal
# path "schedule" is captured by the {sequence_id} route.
@router.get("/sequences/schedule")
async def sequences_schedule(
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    """Per-day count of emails actually SENT (from the tracking log), keyed by YYYY-MM-DD,
    so the Sequences Schedule chart can plot real activity."""
    company_id = str(getattr(current_user, "company_id", ""))
    sent_by_day: dict[str, int] = {}
    for r in _email_tracking().find({"company_id": company_id}, {"created_at": 1, "_id": 0}):
        day = (r.get("created_at") or "")[:10]
        if day:
            sent_by_day[day] = sent_by_day.get(day, 0) + 1
    return {"sent_by_day": sent_by_day}


@router.get("/sequences/{sequence_id}")
async def get_sequence(
    sequence_id: str,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    company_id = str(getattr(current_user, "company_id", ""))
    doc = _sequences().find_one({"sequence_id": sequence_id, "company_id": company_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Sequence not found.")
    return _seq_public(doc)


class SequencePatch(BaseModel):
    name: str | None = None
    steps: list[dict[str, Any]] | None = None
    privacy: str | None = None


@router.patch("/sequences/{sequence_id}")
async def update_sequence(
    sequence_id: str,
    body: SequencePatch,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    company_id = str(getattr(current_user, "company_id", ""))
    set_doc: dict[str, Any] = {"updated_at": datetime.now().isoformat()}
    if body.name is not None:
        set_doc["name"] = body.name
    if body.steps is not None:
        set_doc["steps"] = body.steps
    if body.privacy is not None:
        set_doc["privacy"] = body.privacy
    res = _sequences().update_one({"sequence_id": sequence_id, "company_id": company_id}, {"$set": set_doc})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Sequence not found.")
    return _seq_public(
        _sequences().find_one({"sequence_id": sequence_id, "company_id": company_id}, {"_id": 0}) or {}
    )


@router.delete("/sequences/{sequence_id}")
async def delete_sequence(
    sequence_id: str,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.update))
    ],
):
    company_id = str(getattr(current_user, "company_id", ""))
    _sequences().delete_one({"sequence_id": sequence_id, "company_id": company_id})
    return {"status": "deleted"}


class GenerateSeqBody(BaseModel):
    context: str = ""
    name: str | None = None
    steps: int = 4


@router.post("/sequences/generate")
async def generate_sequence(
    body: GenerateSeqBody,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    """AI-draft a multi-step outreach email sequence from the role/agent context, using merge fields
    like {{First Name}}, {{Current Company}}, {{Job Title}}."""
    import json as _json

    from app.core.ai import client

    n = max(1, min(6, body.steps or 4))
    prompt = (
        f"Draft a {n}-step recruiting outreach EMAIL sequence for this role/context:\n{body.context}\n\n"
        "Step 1 is a cold email; later steps are short follow-ups (type 'reply'). Use a warm, concise, "
        "professional tone. Use merge fields where natural: {{First Name}}, {{Current Company}}, {{Job Title}}, "
        '{{Sender First Name}}. Return JSON: {"steps":[{"type":"email"|"reply","subject":string,'
        '"body":string,"delay_days":number}]}. Step 1 delay_days=0; follow-ups spaced 2-4 days apart.'
    )
    try:
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You output only JSON with a steps array."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.6,
        )
        data = _json.loads(resp.choices[0].message.content or "{}")
        steps = data.get("steps") or []
    except Exception:
        steps = [
            {
                "type": "email",
                "subject": "Exciting opportunity",
                "body": "Hi {{First Name}},\n\nI came across your profile and think you'd be a great fit for a role we're hiring for. Would you be open to a quick chat?\n\nBest,\n{{Sender First Name}}",
                "delay_days": 0,
            }
        ]
    return {"steps": steps}


class GenerateStepBody(BaseModel):
    current_steps: list[dict[str, Any]] = []
    instruction: str = ""


@router.post("/sequences/generate-step")
async def generate_sequence_step(
    body: GenerateStepBody,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    """Generate ONE additional follow-up step from the existing steps + a brief instruction."""
    import json as _json

    from app.core.ai import client

    existing = "\n\n".join(
        f"Step {i + 1} ({s.get('type')}): {s.get('subject', '')}\n{s.get('body', '')[:400]}"
        for i, s in enumerate(body.current_steps)
    )
    prompt = (
        f"Existing outreach steps:\n{existing or '(none)'}\n\n"
        f"Write ONE new follow-up step. Instruction: {body.instruction or 'a natural next follow-up'}. "
        "Keep it short, professional, and use merge fields where natural. Return JSON: "
        '{"type":"reply","subject":string,"body":string,"delay_days":number}.'
    )
    try:
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You output only a single JSON step object."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.6,
        )
        step = _json.loads(resp.choices[0].message.content or "{}")
    except Exception:
        step = {
            "type": "reply",
            "subject": "",
            "body": "Hi {{First Name}},\n\nJust following up on my note — happy to share more whenever the timing works.\n\nBest,\n{{Sender First Name}}",
            "delay_days": 3,
        }
    return {"step": step}


# Sample values used to render merge fields for a preview / test send.
_PREVIEW_SAMPLE = {
    "First Name": "Alex",
    "Current Company": "Acme Corp",
    "Job Title": "Senior Engineer",
    "Current Role": "Senior Engineer",
    "Education": "Stanford University",
    "Sender First Name": "Vibin",
    "Current Location": "San Francisco, CA",
}


def _fill_merge_fields(text: str) -> str:
    """Replace {{Field}} merge tags with sample values, and pick the first spintax option."""
    import re as _re

    out = text or ""
    for key, val in _PREVIEW_SAMPLE.items():
        out = out.replace("{{" + key + "}}", val)
    # {Hi|Hello|Hey} -> first option.
    # Every branch excludes '|' as well as braces: letting the repeated group match '|'
    # too made the alternatives ambiguous, so unclosed input like "{a|b|b|b|b..." forced
    # exponential backtracking (ReDoS). Matching is otherwise unchanged.
    # Both runs are possessive: neither class can match '|', so giving characters back can
    # never help the match - it only made an unclosed "{a|b|b..." retry at every position.
    out = _re.sub(r"\{([^{}|]++(?:\|[^{}|]++)+)\}", lambda m: m.group(1).split("|")[0], out)
    # Any leftover {{...}} -> stripped braces
    out = _re.sub(r"\{\{\s*([^{}]+?)\s*\}\}", r"\1", out)
    return out


class SequenceTestBody(BaseModel):
    to_email: str
    subject: str = ""
    body: str = ""
    sequence_id: str | None = None


def _email_tracking():
    return _db()["email_tracking"]


@router.post("/sequences/test")
async def send_sequence_test(
    body: SequenceTestBody,
    request: Request,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    """Send a single test email for a step, with merge fields filled from sample data.
    Uses the ORGANIZATION'S OWN connected mailbox (not the platform default). If the company
    hasn't connected a mailbox yet, returns 409 so the UI can prompt them to connect one."""
    company_id = str(getattr(current_user, "company_id", ""))
    conn = _active_connection(company_id)
    if not conn:
        raise HTTPException(status_code=409, detail="no_mailbox")

    recipient = (body.to_email or "").strip()
    if not recipient:
        raise HTTPException(status_code=400, detail="No recipient email")
    subject = _fill_merge_fields(body.subject or "(no subject)")
    html = _fill_merge_fields(body.body or "")
    if "<" not in html:
        html = "<p>" + html.replace("\n", "<br/>") + "</p>"

    # Open tracking: embed an invisible 1x1 pixel that pings the backend when the recipient's
    # mail client loads it. Requires a tracking row so the open can be attributed to a sequence.
    track_id = uuid.uuid4().hex
    if body.sequence_id:
        _email_tracking().insert_one(
            {
                "track_id": track_id,
                "sequence_id": body.sequence_id,
                "company_id": company_id,
                "to_email": recipient,
                "opened": False,
                "created_at": datetime.now().isoformat(),
            }
        )
        base = str(request.base_url).rstrip("/")
        pixel = f'<img src="{base}/api/v1/enterprise/sourcing/chat/track/open/{track_id}" width="1" height="1" alt="" style="display:none">'
        html = html + pixel

    ok, err = await _send_mail(conn, recipient, subject, html)
    if not ok:
        raise HTTPException(status_code=502, detail=err or "Failed to send test email")

    # A test send counts as a SENT email — it bumps Total (and marks the sequence Active).
    # Opened / Clicked / Replied / Interested are real recipient events: Opened is now
    # incremented by the tracking pixel below; Clicked / Replied / Interested still need
    # click/reply tracking (not yet implemented).
    if body.sequence_id:
        _sequences().update_one(
            {"sequence_id": body.sequence_id, "company_id": company_id},
            {"$inc": {"stats.total": 1, "stats.active": 1}},
        )

    return {"sent": True, "to": recipient, "from": conn.get("email"), "mailbox": conn.get("email")}


# 1x1 transparent GIF returned to the recipient's mail client for the tracking pixel.
_TRACK_PIXEL = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")


@router.get("/track/open/{track_id}")
async def track_email_open(track_id: str):
    """Public: the tracking pixel URL. First load marks the email opened and bumps the
    sequence's Opened count. Returns a 1x1 transparent GIF."""
    from fastapi import Response

    doc = _email_tracking().find_one({"track_id": track_id})
    if doc and not doc.get("opened"):
        _email_tracking().update_one(
            {"track_id": track_id}, {"$set": {"opened": True, "opened_at": datetime.now().isoformat()}}
        )
        if doc.get("sequence_id"):
            _sequences().update_one(
                {"sequence_id": doc["sequence_id"], "company_id": doc.get("company_id")},
                {"$inc": {"stats.opened": 1}},
            )
    return Response(
        content=_TRACK_PIXEL,
        media_type="image/gif",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache"},
    )


# ---------------------------------------------------------------------------
# Mailbox connections (multi-tenant): each organization connects its OWN mailbox
# (Gmail / Outlook / IMAP-SMTP). Sequences send through the org's mailbox, never
# the platform default. Passwords / app-passwords are stored per company and never
# returned to the client.
# ---------------------------------------------------------------------------
def _connections():
    return _db()["mailbox_connections"]


_PROVIDER_DEFAULTS = {
    "gmail": {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
    },
    "outlook": {
        "smtp_host": "smtp-mail.outlook.com",
        "smtp_port": 587,
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
    },
    "imap": {"smtp_host": "", "smtp_port": 587, "imap_host": "", "imap_port": 993},
}


class MailboxConnectionBody(BaseModel):
    provider: str = "imap"  # gmail | outlook | imap
    email: str
    password: str  # app password / mailbox password
    display_name: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    imap_host: str | None = None
    imap_port: int | None = None
    username: str | None = None  # defaults to email


def _conn_public(doc: dict[str, Any]) -> dict[str, Any]:
    doc = dict(doc)
    for secret in ("_id", "password", "company_id", "refresh_token", "access_token"):
        doc.pop(secret, None)
    doc.setdefault("auth_type", "password")
    return doc


def _active_connection(company_id: str) -> dict[str, Any] | None:
    """Return the most recently connected mailbox for this company (with password)."""
    return _connections().find_one(
        {"company_id": company_id, "status": "connected"}, sort=[("created_at", -1)]
    )


def _smtp_send_via(conn: dict[str, Any], to_email: str, subject: str, html: str) -> tuple[bool, str]:
    """Send one HTML email through the org's connected SMTP mailbox."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    host = str(conn.get("smtp_host") or "")
    port = int(conn.get("smtp_port") or 587)
    user = str(conn.get("username") or conn.get("email") or "")
    pw = str(conn.get("password") or "")
    from_name = conn.get("display_name") or ""
    from_addr = str(conn.get("email") or user)
    try:
        msg = MIMEMultipart()
        msg["From"] = f"{from_name} <{from_addr}>" if from_name else from_addr
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(html, "html"))
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=25) as s:
                s.login(user, pw)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=25) as s:
                s.starttls()
                s.login(user, pw)
                s.send_message(msg)
        return True, ""
    except Exception as e:
        return False, str(e)


def _smtp_verify(conn: dict[str, Any]) -> tuple[bool, str]:
    """Verify SMTP credentials by logging in (no email sent)."""
    import smtplib

    host = str(conn.get("smtp_host") or "")
    port = int(conn.get("smtp_port") or 587)
    user = str(conn.get("username") or conn.get("email") or "")
    pw = str(conn.get("password") or "")
    if not host or not user or not pw:
        return False, "Missing SMTP host, username or password"
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20) as s:
                s.login(user, pw)
        else:
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.starttls()
                s.login(user, pw)
        return True, ""
    except Exception as e:
        return False, str(e)


@router.get("/connections")
async def list_connections(
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    company_id = str(getattr(current_user, "company_id", ""))
    rows = list(_connections().find({"company_id": company_id}).sort("created_at", -1))
    return {
        "connections": [_conn_public(r) for r in rows],
        "connected": any(r.get("status") == "connected" for r in rows),
    }


@router.post("/connections")
async def create_connection(
    body: MailboxConnectionBody,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    """Connect an org mailbox. Verifies the SMTP login before saving."""
    company_id = str(getattr(current_user, "company_id", ""))
    owner = getattr(current_user, "first_name", None) or getattr(current_user, "email", None) or "—"
    provider = (body.provider or "imap").lower()
    defaults = _PROVIDER_DEFAULTS.get(provider, _PROVIDER_DEFAULTS["imap"])
    conn = {
        "connection_id": uuid.uuid4().hex,
        "company_id": company_id,
        "provider": provider,
        "email": (body.email or "").strip(),
        "username": (body.username or body.email or "").strip(),
        "password": body.password or "",
        "display_name": (body.display_name or "").strip() or None,
        "smtp_host": (body.smtp_host or defaults["smtp_host"]).strip(),
        "smtp_port": int(body.smtp_port or defaults["smtp_port"]),
        "imap_host": (body.imap_host or defaults["imap_host"]).strip(),
        "imap_port": int(body.imap_port or defaults["imap_port"]),
        "status": "connected",
        "owner": owner,
        "created_at": datetime.now().isoformat(),
    }
    ok, err = _smtp_verify(conn)
    if not ok:
        raise HTTPException(status_code=400, detail=f"Could not connect: {err}")
    # One mailbox per email address per company — replace if re-connecting.
    _connections().delete_many({"company_id": company_id, "email": conn["email"]})
    _connections().insert_one(conn)
    return _conn_public(conn)


@router.delete("/connections/{connection_id}")
async def delete_connection(
    connection_id: str,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    company_id = str(getattr(current_user, "company_id", ""))
    _connections().delete_one({"connection_id": connection_id, "company_id": company_id})
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Unified send dispatcher — OAuth mailboxes (Gmail) go through the provider API,
# password/app-password mailboxes go through SMTP.
# ---------------------------------------------------------------------------
async def _send_mail(conn: dict[str, Any], to_email: str, subject: str, html: str) -> tuple[bool, str]:
    if conn.get("auth_type") == "oauth" and conn.get("provider") == "gmail":
        return await _gmail_api_send(conn, to_email, subject, html)
    if conn.get("auth_type") == "oauth" and conn.get("provider") == "microsoft":
        return await _ms_graph_send(conn, to_email, subject, html)
    return _smtp_send_via(conn, to_email, subject, html)


# ---------------------------------------------------------------------------
# Microsoft 365 OAuth ("Sign in with Microsoft" — like Juicebox). Azure AD app +
# Microsoft Graph: send via /me/sendMail, read via /me/messages. Needs
# MICROSOFT_CLIENT_ID / MICROSOFT_CLIENT_SECRET (+ optional MICROSOFT_TENANT).
# ---------------------------------------------------------------------------
_MS_SCOPE = (
    "openid email offline_access "
    "https://graph.microsoft.com/Mail.Send "
    "https://graph.microsoft.com/Mail.Read "
    "https://graph.microsoft.com/User.Read"
)
_MS_CALLBACK_PATH = "/api/v1/enterprise/sourcing/chat/connections/microsoft/callback"


def _ms_tenant() -> str:
    from app.core.settings import get_settings

    return get_settings().microsoft_tenant or "common"


def _ms_token_url() -> str:
    return f"https://login.microsoftonline.com/{_ms_tenant()}/oauth2/v2.0/token"


async def _microsoft_access_token(refresh_token: str) -> str:
    """Exchange a stored refresh token for a fresh Microsoft Graph access token."""
    import httpx

    from app.core.settings import get_settings

    s = get_settings()
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(
            _ms_token_url(),
            data={
                "client_id": s.microsoft_client_id,
                "client_secret": s.microsoft_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
                "scope": _MS_SCOPE,
            },
        )
    r.raise_for_status()
    return r.json()["access_token"]


async def _ms_graph_send(conn: dict[str, Any], to_email: str, subject: str, html: str) -> tuple[bool, str]:
    """Send one HTML email via the Microsoft Graph API using the org's OAuth refresh token."""
    import httpx

    try:
        access = await _microsoft_access_token(str(conn.get("refresh_token") or ""))
        payload = {
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": html},
                "toRecipients": [{"emailAddress": {"address": to_email}}],
            },
            "saveToSentItems": True,
        }
        async with httpx.AsyncClient(timeout=25) as c:
            r = await c.post(
                "https://graph.microsoft.com/v1.0/me/sendMail",
                headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json"},
                json=payload,
            )
        if r.status_code >= 300:
            return False, f"Graph API {r.status_code}: {r.text[:200]}"
        return True, ""
    except Exception as e:
        return False, str(e)


async def _google_access_token(refresh_token: str) -> str:
    """Exchange a stored refresh token for a fresh Gmail access token."""
    import httpx

    from app.core.settings import get_settings

    s = get_settings()
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": s.google_client_id,
                "client_secret": s.google_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    r.raise_for_status()
    return r.json()["access_token"]


async def _gmail_api_send(conn: dict[str, Any], to_email: str, subject: str, html: str) -> tuple[bool, str]:
    """Send one HTML email via the Gmail API using the org's OAuth refresh token."""
    import base64
    from email.mime.text import MIMEText

    import httpx

    try:
        access = await _google_access_token(str(conn.get("refresh_token") or ""))
        msg = MIMEText(html, "html")
        msg["To"] = to_email
        from_name = conn.get("display_name") or ""
        from_addr = str(conn.get("email") or "")
        msg["From"] = f"{from_name} <{from_addr}>" if from_name else from_addr
        msg["Subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        async with httpx.AsyncClient(timeout=25) as c:
            r = await c.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                headers={"Authorization": f"Bearer {access}"},
                json={"raw": raw},
            )
        if r.status_code >= 300:
            return False, f"Gmail API {r.status_code}: {r.text[:200]}"
        return True, ""
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# Google OAuth ("Sign in with Google" — like Juicebox). Authorization-code flow
# with the gmail.send scope so we get a refresh token to send on the org's behalf.
# Reuses GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET (also used by login SSO).
# ---------------------------------------------------------------------------
_GMAIL_SCOPE = "openid email https://www.googleapis.com/auth/gmail.send"
_GOOGLE_CALLBACK_PATH = "/api/v1/enterprise/sourcing/chat/connections/google/callback"


def _oauth_states():
    return _db()["oauth_states"]


@router.get("/connections/google/authorize")
async def google_authorize(
    request: Request,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    """Return the Google consent URL the browser should redirect to."""
    from urllib.parse import urlencode

    from app.core.settings import get_settings

    s = get_settings()
    if not s.google_client_id or not s.google_client_secret:
        raise HTTPException(status_code=400, detail="Google OAuth is not configured on the server")
    company_id = str(getattr(current_user, "company_id", ""))
    owner = getattr(current_user, "first_name", None) or getattr(current_user, "email", None) or "—"
    state = uuid.uuid4().hex
    _oauth_states().insert_one(
        {
            "state": state,
            "company_id": company_id,
            "owner": owner,
            "provider": "gmail",
            "created_at": datetime.now().isoformat(),
        }
    )
    redirect_uri = str(request.base_url).rstrip("/") + _GOOGLE_CALLBACK_PATH
    params = {
        "client_id": s.google_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": _GMAIL_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return {"authorize_url": "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)}


@router.get("/connections/google/callback")
async def google_callback(
    request: Request, code: str | None = None, state: str | None = None, error: str | None = None
):
    """Google redirects the browser here. Exchange the code for tokens, store the
    mailbox connection, then bounce back to the Connections page."""
    import base64
    import json as _json

    import httpx
    from fastapi.responses import RedirectResponse

    from app.core.settings import get_settings

    s = get_settings()
    front = s.frontend_url.rstrip("/") + "/enterprise/sourcing/connections"
    if error or not code or not state:
        return RedirectResponse(front + "?connected=0")
    st = _oauth_states().find_one({"state": state})
    if not st:
        return RedirectResponse(front + "?connected=0&reason=state")
    _oauth_states().delete_one({"state": state})
    redirect_uri = str(request.base_url).rstrip("/") + _GOOGLE_CALLBACK_PATH
    try:
        async with httpx.AsyncClient(timeout=25) as c:
            r = await c.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": s.google_client_id,
                    "client_secret": s.google_client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
        tok = r.json()
        if r.status_code >= 300 or not tok.get("refresh_token"):
            return RedirectResponse(front + "?connected=0&reason=token")
        email = ""
        if tok.get("id_token"):
            payload = tok["id_token"].split(".")[1]
            payload += "=" * (-len(payload) % 4)
            email = _json.loads(base64.urlsafe_b64decode(payload)).get("email", "")
        conn = {
            "connection_id": uuid.uuid4().hex,
            "company_id": st["company_id"],
            "provider": "gmail",
            "auth_type": "oauth",
            "email": email,
            "username": email,
            "display_name": None,
            "refresh_token": tok["refresh_token"],
            "access_token": tok.get("access_token"),
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "imap_host": "imap.gmail.com",
            "imap_port": 993,
            "status": "connected",
            "owner": st.get("owner"),
            "created_at": datetime.now().isoformat(),
        }
        _connections().delete_many({"company_id": st["company_id"], "email": email})
        _connections().insert_one(conn)
        return RedirectResponse(front + "?connected=1")
    except Exception:
        return RedirectResponse(front + "?connected=0&reason=error")


@router.get("/connections/microsoft/authorize")
async def microsoft_authorize(
    request: Request,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
):
    """Return the Microsoft consent URL the browser should redirect to."""
    from urllib.parse import urlencode

    from app.core.settings import get_settings

    s = get_settings()
    if not s.microsoft_client_id or not s.microsoft_client_secret:
        raise HTTPException(status_code=400, detail="Microsoft OAuth is not configured on the server")
    company_id = str(getattr(current_user, "company_id", ""))
    owner = getattr(current_user, "first_name", None) or getattr(current_user, "email", None) or "—"
    state = uuid.uuid4().hex
    _oauth_states().insert_one(
        {
            "state": state,
            "company_id": company_id,
            "owner": owner,
            "provider": "microsoft",
            "created_at": datetime.now().isoformat(),
        }
    )
    redirect_uri = str(request.base_url).rstrip("/") + _MS_CALLBACK_PATH
    params = {
        "client_id": s.microsoft_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "response_mode": "query",
        "scope": _MS_SCOPE,
        "prompt": "select_account",
        "state": state,
    }
    base = f"https://login.microsoftonline.com/{_ms_tenant()}/oauth2/v2.0/authorize?"
    return {"authorize_url": base + urlencode(params)}


@router.get("/connections/microsoft/callback")
async def microsoft_callback(
    request: Request, code: str | None = None, state: str | None = None, error: str | None = None
):
    """Microsoft redirects the browser here. Exchange the code for tokens, store the
    mailbox connection, then bounce back to the Connections page."""
    import httpx
    from fastapi.responses import RedirectResponse

    from app.core.settings import get_settings

    s = get_settings()
    front = s.frontend_url.rstrip("/") + "/enterprise/sourcing/connections"
    if error or not code or not state:
        return RedirectResponse(front + "?connected=0")
    st = _oauth_states().find_one({"state": state})
    if not st:
        return RedirectResponse(front + "?connected=0&reason=state")
    _oauth_states().delete_one({"state": state})
    redirect_uri = str(request.base_url).rstrip("/") + _MS_CALLBACK_PATH
    try:
        async with httpx.AsyncClient(timeout=25) as c:
            r = await c.post(
                _ms_token_url(),
                data={
                    "code": code,
                    "client_id": s.microsoft_client_id,
                    "client_secret": s.microsoft_client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                    "scope": _MS_SCOPE,
                },
            )
        tok = r.json()
        if r.status_code >= 300 or not tok.get("refresh_token"):
            return RedirectResponse(front + "?connected=0&reason=token")

        # Resolve the mailbox address from Microsoft Graph /me.
        email = ""
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                me = await c.get(
                    "https://graph.microsoft.com/v1.0/me",
                    headers={"Authorization": f"Bearer {tok.get('access_token')}"},
                )
            mj = me.json()
            email = mj.get("mail") or mj.get("userPrincipalName") or ""
        except Exception:
            email = ""

        conn = {
            "connection_id": uuid.uuid4().hex,
            "company_id": st["company_id"],
            "provider": "microsoft",
            "auth_type": "oauth",
            "email": email,
            "username": email,
            "display_name": None,
            "refresh_token": tok["refresh_token"],
            "access_token": tok.get("access_token"),
            "smtp_host": "smtp-mail.outlook.com",
            "smtp_port": 587,
            "imap_host": "outlook.office365.com",
            "imap_port": 993,
            "status": "connected",
            "owner": st.get("owner"),
            "created_at": datetime.now().isoformat(),
        }
        if email:
            _connections().delete_many({"company_id": st["company_id"], "email": email})
        _connections().insert_one(conn)
        return RedirectResponse(front + "?connected=1")
    except Exception:
        return RedirectResponse(front + "?connected=0&reason=error")
