"""Job-portal integrations: catalog + per-company connections.

Direction is PUBLISH — distributing a Croar job out to external boards. This router
backs the Settings → Job Portals UI:

- ``GET  /job-portals/catalog``      — the portal catalog (KR/JP/global) with honest
  integration metadata, merged with this company's connection status.
- ``GET  /job-portals/connections``  — this company's stored portal connections (secrets stripped).
- ``POST /job-portals/connections``  — connect/update a credentialed portal (e.g. Wanted corporate key).
- ``DELETE /job-portals/connections/{portal}`` — disconnect.

Credentials live in MongoDB (collection ``job_portal_connections``), keyed by company_id,
mirroring the ``mailbox_connections`` pattern in ``sourcing_chat.py`` — no Alembic migration,
secrets never returned to the client.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.dependencies import PermissionChecker
from app.models.shared.constants import ModuleScope, PermissionAction
from app.router.enterprise.sourcing_chat import _db
from app.services.enterprise.job_distribution import job_distribution_service

router = APIRouter(prefix="/job-portals", tags=["Job Portals"])


def _portal_connections():
    return _db()["job_portal_connections"]


def _conn_public(doc: dict[str, Any]) -> dict[str, Any]:
    """Strip secrets before returning a connection to the client."""
    return {
        "portal": doc.get("portal"),
        "display_name": doc.get("display_name"),
        "status": doc.get("status", "connected"),
        "created_at": doc.get("created_at"),
        "has_credentials": bool(doc.get("credentials")),
    }


class PortalConnectionBody(BaseModel):
    portal: str  # catalog key, e.g. "wanted"
    credentials: dict[str, Any] = {}  # e.g. {"corporate_key": "..."}
    display_name: str | None = None


@router.get("/catalog")
async def portal_catalog(
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.read))],
    country: str | None = None,
):
    """Return the job-portal catalog, merged with this company's connection status."""
    company_id = str(getattr(current_user, "company_id", ""))
    connected = {
        c["portal"]: c for c in _portal_connections().find({"company_id": company_id, "status": "connected"})
    }
    out = []
    for meta in job_distribution_service.catalog(country):
        d = meta.as_dict()
        conn = connected.get(meta.key)
        d["connected"] = bool(conn)
        d["connection"] = _conn_public(conn) if conn else None
        out.append(d)
    return {"portals": out}


@router.get("/connections")
async def list_portal_connections(
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.read))],
):
    company_id = str(getattr(current_user, "company_id", ""))
    rows = list(_portal_connections().find({"company_id": company_id}).sort("created_at", -1))
    return {"connections": [_conn_public(r) for r in rows]}


@router.post("/connections")
async def connect_portal(
    body: PortalConnectionBody,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.publish))],
):
    company_id = str(getattr(current_user, "company_id", ""))
    meta = job_distribution_service.meta(body.portal)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Unknown portal '{body.portal}'")
    if not meta.requires_credentials:
        raise HTTPException(
            status_code=400,
            detail=f"{meta.name} does not need a connection — it is distributed via {meta.integration.value}.",
        )
    if not body.credentials:
        raise HTTPException(status_code=422, detail="Missing credentials for this portal.")

    doc = {
        "company_id": company_id,
        "portal": meta.key,
        "credentials": body.credentials,
        "display_name": body.display_name or meta.name,
        "status": "connected",
        "owner": str(getattr(current_user, "id", "") or getattr(current_user, "email", "")),
        "created_at": datetime.now().isoformat(),
    }
    _portal_connections().update_one(
        {"company_id": company_id, "portal": meta.key}, {"$set": doc}, upsert=True
    )
    return {"connected": True, "connection": _conn_public(doc)}


@router.delete("/connections/{portal}")
async def disconnect_portal(
    portal: str,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.jobs, PermissionAction.publish))],
):
    company_id = str(getattr(current_user, "company_id", ""))
    res = _portal_connections().delete_one({"company_id": company_id, "portal": portal})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Connection not found")
    return {"disconnected": True, "portal": portal}


def active_portal_connection(company_id: str, portal: str) -> dict[str, Any] | None:
    """Return a company's stored connection (with credentials) for the publish flow."""
    return _portal_connections().find_one(
        {"company_id": str(company_id), "portal": portal, "status": "connected"}
    )
