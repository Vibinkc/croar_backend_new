"""Per-job sourcing funnel tracker (MongoDB).

Records, for a specific job, the candidates that were SOURCED + INVITED (with the outreach mail's
status) and whether they later APPLIED (filled the job's application form) — so the job detail
page's "Sourcing" tab can show the funnel: Invited (mail sent) -> Applied -> (in the pipeline).

MongoDB (schemaless) is used deliberately so this needs no Alembic migration. Everything is
best-effort: if Mongo is unavailable the calls become no-ops and the rest of the app is unaffected.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

try:
    from pymongo import MongoClient
    from pymongo.collection import Collection
except Exception:  # pragma: no cover - pymongo always installed in prod
    MongoClient = None  # type: ignore[assignment,misc]
    Collection = Any  # type: ignore[assignment,misc]

_COLLECTION = "job_sourced_candidates"
_client: Any = None
_indexes_ready = False


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _collection() -> Collection | None:
    """Return the job-sourcing collection, or None if Mongo is unavailable."""
    global _client, _indexes_ready
    if MongoClient is None:
        return None
    try:
        if _client is None:
            uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
            _client = MongoClient(uri, serverSelectionTimeoutMS=1500)
        db = _client[os.getenv("MONGO_DB_NAME", "croar_sourcing")]
        coll = db[_COLLECTION]
        if not _indexes_ready:
            try:
                coll.create_index([("job_id", 1), ("key", 1)], unique=True)
                coll.create_index("job_id")
                _indexes_ready = True
            except Exception:
                pass
        return coll
    except Exception:
        return None


def _key(email: str | None, profile_url: str | None) -> str | None:
    """Stable per-candidate key within a job: prefer email, else the profile URL."""
    e = (email or "").strip().lower()
    if e:
        return f"email:{e}"
    u = (profile_url or "").strip().lower()
    return f"url:{u}" if u else None


def record_invites(job_id: str, company_id: str | None, items: list[dict[str, Any]]) -> int:
    """Upsert the candidates just invited for a job, with each one's mail-sent status.

    `items` = dicts with: full_name, email, headline, platform, profile_url, location,
    invite_status ('sent' | 'failed'). Returns how many rows were written."""
    coll = _collection()
    if coll is None or not items:
        return 0
    now = _now()
    written = 0
    for it in items:
        key = _key(it.get("email"), it.get("profile_url"))
        if not key:
            continue
        try:
            coll.update_one(
                {"job_id": str(job_id), "key": key},
                {
                    "$set": {
                        "company_id": str(company_id) if company_id else None,
                        "full_name": it.get("full_name") or it.get("name"),
                        "email": (it.get("email") or "").strip() or None,
                        "headline": it.get("headline"),
                        "platform": it.get("platform"),
                        "profile_url": it.get("profile_url"),
                        "location": it.get("location"),
                        "invite_status": it.get("invite_status") or "sent",
                        "invited_at": now,
                        "updated_at": now,
                    },
                    "$setOnInsert": {
                        "applied": False,
                        "applied_at": None,
                        "application_id": None,
                        "created_at": now,
                    },
                },
                upsert=True,
            )
            written += 1
        except Exception:
            logger.debug("Skipping a sourced-profile upsert that failed", exc_info=True)
            continue
    return written


def record_shortlist(job_id: str, company_id: str | None, profile: dict[str, Any]) -> bool:
    """Add a candidate SHORTLISTED from Profile Sourcing to a job's sourced-candidates list, so it
    shows on the job detail page's "Profile Sourcing" tab. No outreach mail has been sent yet
    (status 'shortlisted'); if the candidate was already invited, that existing status is preserved."""
    coll = _collection()
    if coll is None:
        return False
    key = _key(profile.get("email"), profile.get("profile_url"))
    if not key:
        return False
    now = _now()
    try:
        coll.update_one(
            {"job_id": str(job_id), "key": key},
            {
                "$set": {
                    "company_id": str(company_id) if company_id else None,
                    "full_name": profile.get("full_name") or profile.get("name"),
                    "email": (profile.get("email") or "").strip() or None,
                    "headline": profile.get("headline"),
                    "platform": profile.get("platform"),
                    "profile_url": profile.get("profile_url"),
                    "location": profile.get("location"),
                    "shortlisted": True,
                    "shortlisted_at": now,
                    "updated_at": now,
                },
                # Only set these when the row is NEW — so re-shortlisting an already-invited candidate
                # doesn't wipe their invite/applied status.
                "$setOnInsert": {
                    "invite_status": "shortlisted",
                    "invited_at": None,
                    "applied": False,
                    "applied_at": None,
                    "application_id": None,
                    "created_at": now,
                },
            },
            upsert=True,
        )
        return True
    except Exception:
        return False


def mark_invite_sent(job_id: str, email: str | None, profile_url: str | None, ok: bool) -> bool:
    """Flip a sourced candidate's outreach status after the recruiter sends the invite mail from the
    Profile Sourcing tab. Only touches the mail status (keyed by job + email/profile_url) so the rest
    of the row — name, platform, applied flag — is preserved."""
    coll = _collection()
    if coll is None:
        return False
    key = _key(email, profile_url)
    if not key:
        return False
    now = _now()
    try:
        res = coll.update_one(
            {"job_id": str(job_id), "key": key},
            {"$set": {"invite_status": "sent" if ok else "failed", "invited_at": now, "updated_at": now}},
        )
        return res.modified_count > 0
    except Exception:
        return False


def mark_applied(job_id: str, email: str | None, application_id: str | None) -> bool:
    """Flag the sourced candidate (matched by job + email) as having applied / filled the form."""
    coll = _collection()
    if coll is None:
        return False
    key = _key(email, None)
    if not key:
        return False
    try:
        res = coll.update_one(
            {"job_id": str(job_id), "key": key},
            {
                "$set": {
                    "applied": True,
                    "applied_at": _now(),
                    "application_id": str(application_id) if application_id else None,
                    "updated_at": _now(),
                }
            },
        )
        return res.modified_count > 0
    except Exception:
        return False


def list_for_job(job_id: str) -> list[dict[str, Any]]:
    """All tracked sourced candidates for a job, newest-invited first (for the Sourcing tab)."""
    coll = _collection()
    if coll is None:
        return []
    try:
        # Sort by updated_at (all rows have it) so shortlisted candidates — which have no invited_at
        # yet — still appear by recency alongside invited ones.
        rows = list(coll.find({"job_id": str(job_id)}, {"_id": 0}).sort("updated_at", -1))
        return rows
    except Exception:
        return []
