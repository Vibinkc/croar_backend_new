"""Connect and disconnect third-party tools, in one place.

Deliberately mirrors job_portals: the same Mongo-backed per-company connection store, the same
"strip the secrets on the way out" rule. Job boards keep their own richer endpoints (they have
publish semantics); this covers everything else and lists the boards alongside so the UI has a
single integrations area rather than two half-catalogues.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.dependencies import PermissionChecker
from app.models.shared.constants import ModuleScope, PermissionAction
from app.router.enterprise.sourcing_chat import _db
from app.services.enterprise import integrations as catalog_service
from app.services.enterprise.job_distribution import job_distribution_service

router = APIRouter(prefix="/integrations", tags=["Enterprise Integrations"])


async def _verify_credentials(key: str, creds: dict[str, str]) -> tuple[bool, str]:
    """Ask the provider whether the key is real, for the integrations whose API is free.

    A connection that only stores a string is not a connection — the first sign of a typo would
    otherwise be a candidate never receiving their test. Providers without a free API are stored
    unverified, and their cards say so.
    """
    import httpx

    if key == "jotform":
        api_key = (creds.get("api_key") or "").strip()
        if not api_key:
            return False, "An API key is required."
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                r = await client.get("https://api.jotform.com/user", params={"apiKey": api_key})
            if r.status_code == 200:
                return True, ""
            if r.status_code in (401, 403):
                return False, "Jotform rejected that API key."
            return False, f"Jotform returned {r.status_code}."
        except Exception as e:  # network trouble is not a bad key — say which it was
            return False, f"Could not reach Jotform to check the key: {e!s}"

    # No free API to check against; store as given.
    return True, ""


def _connections():
    return _db()["integration_connections"]


def _public(doc: dict[str, Any]) -> dict[str, Any]:
    """Never return stored credentials — only that they exist."""
    creds = doc.get("credentials") or {}
    return {
        "integration": doc.get("integration"),
        "display_name": doc.get("display_name"),
        "status": doc.get("status", "connected"),
        "created_at": doc.get("created_at"),
        # The invite URL is not a secret and the round builder needs it to describe itself.
        "invite_url": creds.get("invite_url"),
        "has_credentials": bool(creds),
        # True only when the provider itself confirmed the key.
        "verified": bool(doc.get("verified")),
    }


class ConnectBody(BaseModel):
    integration: str
    credentials: dict[str, str] = {}
    display_name: str | None = None


@router.get("/catalog")
async def integration_catalog(
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.read))],
    category: str | None = None,
) -> dict[str, Any]:
    """Every integration, with this company's connection status merged in."""
    company_id = str(getattr(current_user, "company_id", ""))
    connected = {c["integration"]: c for c in _connections().find({"company_id": company_id})}

    items = []
    for meta in catalog_service.catalog(category):
        d = meta.as_dict()
        conn = connected.get(meta.key)
        d["connected"] = bool(conn)
        d["connection"] = _public(conn) if conn else None
        items.append(d)

    # Job boards live in their own catalogue because they carry publish semantics, but they are
    # integrations too — listing them here keeps "what are we connected to?" a single question.
    boards = []
    if not category or category == "job_board":
        for meta in job_distribution_service.catalog():
            boards.append(
                {
                    "key": meta.key,
                    "name": meta.name,
                    "category": "job_board",
                    "summary": meta.note or f"Publish jobs to {meta.name}.",
                    "requires_credentials": meta.requires_credentials,
                    "docs_url": meta.docs_url,
                    "manage_url": "/enterprise/settings/job-portals",
                }
            )

    return {"integrations": items, "job_boards": boards}


@router.post("/connections", status_code=201)
async def connect_integration(
    body: ConnectBody,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.update))
    ],
) -> dict[str, Any]:
    company_id = str(getattr(current_user, "company_id", ""))
    meta = catalog_service.meta(body.integration)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Unknown integration '{body.integration}'")

    missing = [
        f.label for f in meta.fields if f.required and not (body.credentials.get(f.name) or "").strip()
    ]
    if missing:
        raise HTTPException(status_code=422, detail=f"Missing required field(s): {', '.join(missing)}")

    verified = False
    if meta.api_tier == "free":
        ok, why = await _verify_credentials(meta.key, body.credentials)
        if not ok:
            raise HTTPException(status_code=422, detail=why)
        verified = True

    doc = {
        "company_id": company_id,
        "integration": meta.key,
        "verified": verified,
        "display_name": (body.display_name or body.credentials.get("display_name") or meta.name).strip(),
        "credentials": {k: v for k, v in body.credentials.items() if v},
        "status": "connected",
        "created_at": datetime.now(UTC),
    }
    _connections().update_one({"company_id": company_id, "integration": meta.key}, {"$set": doc}, upsert=True)
    return {"status": "connected", "connection": _public(doc)}


@router.delete("/connections/{integration}")
async def disconnect_integration(
    integration: str,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.update))
    ],
) -> dict[str, str]:
    company_id = str(getattr(current_user, "company_id", ""))
    res = _connections().delete_one({"company_id": company_id, "integration": integration})
    if not res.deleted_count:
        raise HTTPException(status_code=404, detail="That integration is not connected.")
    return {"status": "disconnected"}


@router.get("/connections")
async def list_connections(
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.read))],
) -> dict[str, Any]:
    """Connected tools only — what a round builder offers as a choice."""
    company_id = str(getattr(current_user, "company_id", ""))
    rows = list(_connections().find({"company_id": company_id}))
    return {"connections": [_public(r) for r in rows]}
