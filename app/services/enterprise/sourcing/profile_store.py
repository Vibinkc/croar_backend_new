"""Persistent, deduplicated store for sourced candidate profiles.

Why this exists
---------------
Historically every sourcing search re-scraped the internet from scratch, which was
slow, produced duplicates, and threw away everything we'd already found. This store
gives us a durable "person" record so that:

  * the same profile is stored **once** (dedup by a stable identity key),
  * we remember **which source(s)** each profile came from (`sources`),
  * profiles can be **tagged** (`tags`) and reused,
  * repeat searches **merge** into the existing record instead of duplicating it.

It is intentionally defensive: if MongoDB is unavailable the methods degrade to
no-ops / empty results so live scraping still works.

Backed by MongoDB (same instance the rest of the sourcing flow already uses:
env `MONGO_URI`, db `MONGO_DB_NAME`, default `croar_sourcing`).
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import UTC, datetime
from typing import Any

try:
    from pymongo import MongoClient
    from pymongo.collection import Collection
except Exception:  # pragma: no cover - pymongo should be installed
    MongoClient = None  # type: ignore[assignment,misc]
    Collection = Any  # type: ignore[assignment,misc]

_COLLECTION = "sourced_profiles"
_client: Any = None
_indexes_ready = False


def _now() -> datetime:
    return datetime.now(UTC)


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip()).lower()


def dedup_key(p: dict[str, Any]) -> str:
    """Stable identity for a profile. Prefers profile_url, then email, then name+platform."""
    url = _norm(p.get("profile_url")).rstrip("/")
    if url:
        return f"url:{url}"
    email = _norm(p.get("email"))
    if email:
        return f"email:{email}"
    return f"np:{_norm(p.get('platform'))}:{_norm(p.get('full_name'))}"


def public_id(key: str) -> str:
    """A short, path-safe id derived from the dedup key (used by the tagging endpoint).

    Not security-sensitive — this is a stable identifier, not a password/token hash —
    so SHA-1 is fine (usedforsecurity=False keeps the security linter honest).
    """
    return hashlib.sha1(key.encode("utf-8"), usedforsecurity=False).hexdigest()[:20]


def _collection() -> Collection | None:
    """Return the profiles collection, or None if Mongo is unavailable."""
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
                coll.create_index("tags")
                coll.create_index("sources")
                coll.create_index("companies")
                coll.create_index("last_seen_at")
                coll.create_index([("full_name", "text"), ("headline", "text"), ("skills", "text")])
                _indexes_ready = True
            except Exception:
                # Index creation is best-effort; searches still work without it.
                pass
        return coll
    except Exception:
        return None


# ── Write path ──────────────────────────────────────────────────────────────


def upsert_many(
    profiles: list[dict[str, Any]], query_text: str = "", company_id: str | None = None
) -> list[dict[str, Any]]:
    """Persist/merge a batch of scraped profiles. Returns the stored records
    (deduped, with `id`, merged `sources`, and any existing `tags`).

    Existing tags are preserved; sources and the surfacing query are accumulated.
    When `company_id` is given it is recorded on the profile's `companies` set, so we
    can later answer "has THIS company already sourced this person?" (the client-DB tier).
    """
    company_id = company_id.strip() if company_id else ""
    coll = _collection()
    if coll is None:
        # No store available — still return the input with a computed id so callers
        # get a consistent shape.
        out = []
        for p in profiles or []:
            key = dedup_key(p)
            out.append(
                {
                    **p,
                    "id": public_id(key),
                    "sources": [p.get("platform")] if p.get("platform") else [],
                    "tags": [],
                    "last_scraped_at": None,
                }
            )
        return out

    stored: list[dict[str, Any]] = []
    now = _now()
    for p in profiles or []:
        key = dedup_key(p)
        pid = public_id(key)
        platform = p.get("platform")

        # Read the prior state so we can record accurate history events (what changed).
        try:
            before = coll.find_one({"_id": pid})
        except Exception:
            before = None

        events: list[dict[str, Any]] = []
        if before is None:
            events.append(
                {"at": now, "type": "sourced", "detail": {"source": platform, "query": _norm(query_text)}}
            )
        else:
            if platform and platform not in set(before.get("sources") or []):
                events.append({"at": now, "type": "source_added", "detail": {"source": platform}})
            if p.get("email") and not before.get("email"):
                events.append({"at": now, "type": "email_found", "detail": {"email": p.get("email")}})

        set_fields = {
            "key": key,
            "full_name": p.get("full_name"),
            "headline": p.get("headline"),
            "location": p.get("location"),
            "skills": p.get("skills") or [],
            "profile_url": p.get("profile_url"),
            "platform": platform,
            "social_links": p.get("social_links") or [],
            "raw_data": p.get("raw_data"),
            "last_seen_at": now,
        }
        # Only overwrite email if we actually have one (don't clobber a known email with null).
        if p.get("email"):
            set_fields["email"] = p.get("email")

        add_to_set: dict[str, Any] = {}
        if platform:
            add_to_set["sources"] = platform
        if query_text:
            add_to_set["query_terms"] = _norm(query_text)
        # Track which companies have sourced this person (client-DB tier lookup).
        if company_id:
            add_to_set["companies"] = company_id

        update: dict[str, Any] = {
            "$set": set_fields,
            # tags/status/history are NOT touched on update, so they survive re-scrapes.
            "$setOnInsert": {"_id": pid, "id": pid, "tags": [], "status": "sourced", "first_seen_at": now},
        }
        if add_to_set:
            update["$addToSet"] = add_to_set
        if events:
            update["$push"] = {"history": {"$each": events, "$slice": -100}}

        try:
            doc = coll.find_one_and_update(
                {"_id": pid},
                update,
                upsert=True,
                return_document=True,  # ReturnDocument.AFTER == True
            )
        except Exception:
            doc = None

        if doc:
            stored.append(_present(doc))
        else:
            stored.append(
                {
                    **p,
                    "id": pid,
                    "sources": [platform] if platform else [],
                    "tags": [],
                    "status": "sourced",
                    "last_scraped_at": now.isoformat(),
                }
            )

    return stored


# ── Read path ───────────────────────────────────────────────────────────────


def search(
    role_terms: list[str] | None,
    location: str | None,
    limit: int = 20,
    company_id: str | None = None,
    exclude_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return previously-stored profiles that match the role terms / location.

    This is the "check our own store first" step — surfaces people we've already
    sourced without hitting the internet again.

    * `company_id` set  → **client-DB tier**: only profiles THIS company has sourced
      before (its own sourcing history for the same role).
    * `company_id` None → **Croar-DB tier**: the global pool across every company.
    * `exclude_ids`     → skip profiles already returned by an earlier tier (dedup).
    """
    coll = _collection()
    if coll is None:
        return []

    terms = [t for t in (role_terms or []) if t and t.strip()]
    ors: list[dict[str, Any]] = []
    for t in terms:
        rx = {"$regex": re.escape(t.strip()), "$options": "i"}
        ors.extend([{"full_name": rx}, {"headline": rx}, {"skills": rx}])

    query: dict[str, Any] = {}
    if ors:
        query["$or"] = ors
    if location and location.strip() and location.strip().lower() not in ("global", "any", "remote"):
        query["location"] = {"$regex": re.escape(location.strip()), "$options": "i"}
    if company_id and company_id.strip():
        query["companies"] = company_id.strip()
    if exclude_ids:
        query["_id"] = {"$nin": list(exclude_ids)}

    try:
        cursor = coll.find(query).sort("last_seen_at", -1).limit(max(1, limit))
        return [_present(doc) for doc in cursor]
    except Exception:
        return []


def _clean_tags(tags: list[str] | None) -> list[str]:
    return sorted({t.strip() for t in (tags or []) if isinstance(t, str) and t.strip()})


def _history_event(kind: str, detail: Any) -> dict[str, Any]:
    return {"at": _now(), "type": kind, "detail": detail}


def _present(doc: dict[str, Any]) -> dict[str, Any]:
    """Drop the Mongo `_id` and surface a UI-friendly `last_scraped_at` (ISO string).

    The stored freshness field is `last_seen_at` (a datetime); we expose it as
    `last_scraped_at` so callers/UI can show "when we last pulled this profile".
    """
    doc.pop("_id", None)
    seen = doc.get("last_seen_at")
    doc["last_scraped_at"] = seen.isoformat() if isinstance(seen, datetime) else seen
    return doc


def _strip(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    return _present(doc) if doc else doc


# ── Tagging (incremental — tags survive re-scrapes) ──────────────────────────


def add_tags(profile_id: str, tags: list[str]) -> dict[str, Any] | None:
    """Add tags to a profile without removing existing ones. Records history."""
    coll = _collection()
    if coll is None:
        return None
    clean = _clean_tags(tags)
    if not clean:
        return get_profile(profile_id)
    try:
        return _strip(
            coll.find_one_and_update(
                {"_id": profile_id},
                {
                    "$addToSet": {"tags": {"$each": clean}},
                    "$set": {"last_seen_at": _now()},
                    "$push": {
                        "history": {"$each": [_history_event("tags_added", {"tags": clean})], "$slice": -100}
                    },
                },
                return_document=True,
            )
        )
    except Exception:
        return None


def remove_tags(profile_id: str, tags: list[str]) -> dict[str, Any] | None:
    """Remove specific tags from a profile. Records history."""
    coll = _collection()
    if coll is None:
        return None
    clean = _clean_tags(tags)
    if not clean:
        return get_profile(profile_id)
    try:
        return _strip(
            coll.find_one_and_update(
                {"_id": profile_id},
                {
                    "$pull": {"tags": {"$in": clean}},
                    "$set": {"last_seen_at": _now()},
                    "$push": {
                        "history": {
                            "$each": [_history_event("tags_removed", {"tags": clean})],
                            "$slice": -100,
                        }
                    },
                },
                return_document=True,
            )
        )
    except Exception:
        return None


def set_tags(profile_id: str, tags: list[str]) -> dict[str, Any] | None:
    """Replace the entire tag set on a profile. Records history."""
    coll = _collection()
    if coll is None:
        return None
    clean = _clean_tags(tags)
    try:
        return _strip(
            coll.find_one_and_update(
                {"_id": profile_id},
                {
                    "$set": {"tags": clean, "last_seen_at": _now()},
                    "$push": {
                        "history": {"$each": [_history_event("tags_set", {"tags": clean})], "$slice": -100}
                    },
                },
                return_document=True,
            )
        )
    except Exception:
        return None


# ── Lifecycle status (part of profile history) ───────────────────────────────


def set_status(profile_id: str, status: str) -> dict[str, Any] | None:
    """Move a profile through its lifecycle (sourced → shortlisted → contacted → …)."""
    coll = _collection()
    if coll is None:
        return None
    status = (status or "").strip().lower()
    if not status:
        return get_profile(profile_id)
    try:
        return _strip(
            coll.find_one_and_update(
                {"_id": profile_id},
                {
                    "$set": {"status": status, "last_seen_at": _now()},
                    "$push": {
                        "history": {
                            "$each": [_history_event("status_changed", {"status": status})],
                            "$slice": -100,
                        }
                    },
                },
                return_document=True,
            )
        )
    except Exception:
        return None


# ── Reads ────────────────────────────────────────────────────────────────────


def get_profile(profile_id: str) -> dict[str, Any] | None:
    """Fetch a single stored profile (with its full tags + history timeline)."""
    coll = _collection()
    if coll is None:
        return None
    try:
        return _strip(coll.find_one({"_id": profile_id}))
    except Exception:
        return None


def list_by_tag(tag: str, limit: int = 50) -> list[dict[str, Any]]:
    coll = _collection()
    if coll is None:
        return []
    try:
        cursor = coll.find({"tags": tag}).sort("last_seen_at", -1).limit(max(1, limit))
        return [_present(doc) for doc in cursor]
    except Exception:
        return []
