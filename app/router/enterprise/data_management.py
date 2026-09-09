"""GDPR consent tracking and bulk candidate import.

Two of the four Data Management items Manatal has and Croar did not.

**Consent** is four columns rather than a boolean, because "do we have consent" is not the
question a regulator asks. They ask when it was given, how it was obtained, and when the lawful
basis lapses. A bare true/false answers none of those, and cannot distinguish "they said no"
from "nobody has asked" — which is the state every web-sourced candidate starts in and the one
worth being able to find.

**Import** is CSV only, and deliberately two-step: upload returns a preview of what WOULD happen,
including which rows are duplicates and which are malformed, and nothing is written until a
second call confirms. A bulk import that half-succeeds and reports a number is the worst
possible outcome — you cannot tell what landed without reading the whole table afterwards.
"""

import csv
import io
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.candidate import Candidate
from app.models.shared.constants import ModuleScope, PermissionAction

router = APIRouter(prefix="/data-management", tags=["Data Management"])

# NULL is a fifth state and the most common one: never asked.
CONSENT_STATUSES = ("granted", "refused", "withdrawn")
CONSENT_SOURCES = ("apply_form", "email_reply", "recorded_manually", "imported")

# Columns the importer understands. Everything else in the file is ignored rather than rejected,
# so an export from another ATS with forty columns still works.
IMPORT_COLUMNS = ("full_name", "email", "phone", "skills", "source_platform", "total_experience")


def _company(user: object) -> Any:
    cid = getattr(user, "company_id", None)
    if not cid:
        raise HTTPException(status_code=404, detail="No company on this account.")
    return cid


def _now() -> datetime:
    """Naive UTC — these columns are TIMESTAMP WITHOUT TIME ZONE."""
    return datetime.now(UTC).replace(tzinfo=None)


# ─────────────────────────────── consent ────────────────────────────────────


class ConsentIn(BaseModel):
    status: Literal["granted", "refused", "withdrawn"]
    source: str = Field(default="recorded_manually", max_length=50)
    note: str | None = None
    # How long the lawful basis lasts. Null means no expiry was set, which is a choice someone
    # made rather than an oversight — the UI says so.
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


@router.get("/consent")
async def list_consent(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
    status: Literal["granted", "refused", "withdrawn", "never_asked", "expired", "expiring"] | None = None,
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
) -> dict[str, Any]:
    """Consent state for every candidate, filterable by the states that need action."""
    cid = _company(current_user)
    now = _now()
    where = [Candidate.company_id == cid, Candidate.deleted_at.is_(None)]

    if status == "never_asked":
        where.append(Candidate.consent_status.is_(None))
    elif status == "expired":
        where += [Candidate.consent_expires_at.is_not(None), Candidate.consent_expires_at < now]
    elif status == "expiring":
        # The next 30 days — the window in which someone can still do something about it.
        where += [
            Candidate.consent_expires_at.is_not(None),
            Candidate.consent_expires_at >= now,
            Candidate.consent_expires_at < now + timedelta(days=30),
        ]
    elif status:
        where.append(Candidate.consent_status == status)

    if q and q.strip():
        like = f"%{q.strip()}%"
        where.append(or_(Candidate.full_name.ilike(like), Candidate.email.ilike(like)))

    total = (await session.execute(select(func.count(Candidate.id)).where(and_(*where)))).scalar_one()
    rows = (
        (
            await session.execute(
                select(Candidate)
                .where(and_(*where))
                .order_by(Candidate.consent_expires_at.asc().nullslast(), Candidate.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "results": [
            {
                "id": str(c.id),
                "full_name": c.full_name,
                "email": c.email,
                "source_platform": c.source_platform,
                "consent_status": c.consent_status,
                "consent_at": c.consent_at.isoformat() if c.consent_at else None,
                "consent_source": c.consent_source,
                "consent_expires_at": c.consent_expires_at.isoformat() if c.consent_expires_at else None,
                "consent_note": c.consent_note,
                "expired": bool(c.consent_expires_at and c.consent_expires_at < now),
            }
            for c in rows
        ],
    }


@router.get("/consent/summary")
async def consent_summary(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
) -> dict[str, Any]:
    """Counts per state, including the two that mean somebody has to do something."""
    cid = _company(current_user)
    now = _now()
    scope = [Candidate.company_id == cid, Candidate.deleted_at.is_(None)]

    async def count(*extra: Any) -> int:
        return (
            await session.execute(select(func.count(Candidate.id)).where(and_(*scope, *extra)))
        ).scalar_one()

    return {
        "granted": await count(Candidate.consent_status == "granted"),
        "refused": await count(Candidate.consent_status == "refused"),
        "withdrawn": await count(Candidate.consent_status == "withdrawn"),
        "never_asked": await count(Candidate.consent_status.is_(None)),
        "expired": await count(Candidate.consent_expires_at.is_not(None), Candidate.consent_expires_at < now),
        "expiring": await count(
            Candidate.consent_expires_at.is_not(None),
            Candidate.consent_expires_at >= now,
            Candidate.consent_expires_at < now + timedelta(days=30),
        ),
        "total": await count(),
    }


@router.post("/consent/{candidate_id}")
async def record_consent(
    candidate_id: UUID,
    body: ConsentIn,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.update))
    ],
) -> dict[str, Any]:
    """Record consent, refusal or withdrawal against one candidate."""
    cid = _company(current_user)
    c = (
        await session.execute(
            select(Candidate).where(
                Candidate.id == candidate_id, Candidate.company_id == cid, Candidate.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    now = _now()
    c.consent_status = body.status
    c.consent_at = now
    c.consent_source = body.source[:50]
    c.consent_note = (body.note or "").strip() or None
    # Refusal and withdrawal have no expiry: there is nothing left to lapse, and leaving an old
    # expiry date on a withdrawn record would make it look like consent returns by itself.
    c.consent_expires_at = (
        now + timedelta(days=body.expires_in_days)
        if body.status == "granted" and body.expires_in_days
        else None
    )
    await session.commit()
    return {
        "id": str(c.id),
        "consent_status": c.consent_status,
        "consent_at": c.consent_at.isoformat(),
        "consent_expires_at": c.consent_expires_at.isoformat() if c.consent_expires_at else None,
    }


# ──────────────────────────────── import ────────────────────────────────────


def _parse_csv(raw: bytes) -> tuple[list[dict[str, str]], list[str]]:
    r"""Read the file, or say why it could not be read.

    utf-8-sig rather than utf-8: spreadsheets export a byte-order mark by default, and without
    stripping it the first column header becomes "\\ufefffull_name" and silently never matches.
    """
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("latin-1")
        except Exception as exc:  # pragma: no cover - defensive
            raise HTTPException(status_code=422, detail="That file is not readable text.") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=422, detail="That file has no header row.")
    headers = [(h or "").strip().lower().replace(" ", "_") for h in reader.fieldnames]
    rows = []
    for row in reader:
        rows.append({(k or "").strip().lower().replace(" ", "_"): (v or "").strip() for k, v in row.items()})
    return rows, headers


def _clean(row: dict[str, str]) -> dict[str, Any]:
    skills = [s.strip() for s in (row.get("skills") or "").replace(";", ",").split(",") if s.strip()]
    exp = row.get("total_experience") or ""
    try:
        years = int(float(exp)) if exp else None
    except ValueError:
        years = None
    return {
        "full_name": (row.get("full_name") or row.get("name") or "").strip(),
        "email": (row.get("email") or "").strip().lower() or None,
        "phone": (row.get("phone") or "").strip() or None,
        "skills": skills,
        "source_platform": (row.get("source_platform") or row.get("source") or "Imported")[:50],
        "total_experience": years,
    }


@router.post("/import/preview")
async def import_preview(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.create))
    ],
    file: Annotated[UploadFile, File()],
) -> dict[str, Any]:
    """Say what an import WOULD do, without doing any of it.

    The whole point of the two-step: a bulk import that half-succeeds and reports a number
    leaves you unable to tell what landed without reading the entire table afterwards.
    """
    cid = _company(current_user)
    raw = await file.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="That file is larger than 5 MB.")
    rows, headers = _parse_csv(raw)
    if not rows:
        raise HTTPException(status_code=422, detail="That file has a header but no rows.")

    existing = {
        e.lower()
        for e in (
            await session.execute(
                select(Candidate.email).where(
                    Candidate.company_id == cid, Candidate.deleted_at.is_(None), Candidate.email.is_not(None)
                )
            )
        )
        .scalars()
        .all()
        if e
    }

    seen: set[str] = set()
    new, updates, invalid = [], [], []
    for i, raw_row in enumerate(rows, start=2):  # row 1 is the header, so people can find it
        c = _clean(raw_row)
        if not c["full_name"]:
            invalid.append({"row": i, "reason": "No name", "data": c})
            continue
        if c["email"] and "@" not in c["email"]:
            invalid.append({"row": i, "reason": "Email is not an address", "data": c})
            continue
        if c["email"] and c["email"] in seen:
            invalid.append({"row": i, "reason": "Repeated inside this file", "data": c})
            continue
        if c["email"]:
            seen.add(c["email"])
        (updates if c["email"] and c["email"] in existing else new).append({"row": i, "data": c})

    return {
        "filename": file.filename,
        "headers": headers,
        "recognised_columns": [h for h in headers if h in IMPORT_COLUMNS or h in ("name", "source")],
        # Named so the UI can say which columns were ignored rather than leaving the user to
        # wonder why a field did not arrive.
        "ignored_columns": [h for h in headers if h not in IMPORT_COLUMNS and h not in ("name", "source")],
        "total_rows": len(rows),
        "will_create": len(new),
        "will_update": len(updates),
        "invalid": len(invalid),
        "sample_create": new[:10],
        "sample_update": updates[:10],
        "invalid_rows": invalid[:20],
        # The rows the commit step will be handed back verbatim. Parsing happens once, here, so
        # the file the user previewed is exactly the file that gets written — a second parse in
        # the browser could disagree with this one and nobody would know which was right.
        "rows": [r["data"] for r in new + updates][:5000],
        "truncated": len(new) + len(updates) > 5000,
    }


class ImportRow(BaseModel):
    full_name: str
    email: str | None = None
    phone: str | None = None
    skills: list[str] = []
    source_platform: str = "Imported"
    total_experience: int | None = None


class ImportCommit(BaseModel):
    rows: list[ImportRow] = Field(min_length=1, max_length=5000)
    # Matching the Sourcing Hub's rule, so an import behaves the same way as every other path
    # that can meet someone twice.
    update_existing: bool = True
    # Imported candidates are recorded as consent-unknown unless the file says otherwise, which
    # is the honest default: possessing a spreadsheet is not consent.
    consent_status: Literal["granted", "refused", "withdrawn"] | None = None


@router.post("/import/commit", status_code=201)
async def import_commit(
    body: ImportCommit,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.create))
    ],
) -> dict[str, Any]:
    """Write the rows the preview described."""
    cid = _company(current_user)
    now = _now()

    by_email = {
        (c.email or "").lower(): c
        for c in (
            await session.execute(
                select(Candidate).where(
                    Candidate.company_id == cid, Candidate.deleted_at.is_(None), Candidate.email.is_not(None)
                )
            )
        )
        .scalars()
        .all()
    }

    created = updated = skipped = 0
    for r in body.rows:
        email = (r.email or "").strip().lower() or None
        existing = by_email.get(email) if email else None
        if existing and not body.update_existing:
            skipped += 1
            continue
        if existing:
            # Fill gaps only. An import must never overwrite a field a recruiter corrected by
            # hand — the spreadsheet is usually the older source.
            if not existing.phone and r.phone:
                existing.phone = r.phone
            if not existing.skills and r.skills:
                existing.skills = r.skills[:40]
            if existing.total_experience is None and r.total_experience is not None:
                existing.total_experience = r.total_experience
            updated += 1
            continue
        c = Candidate(
            full_name=r.full_name.strip(),
            email=email,
            phone=r.phone,
            skills=r.skills[:40],
            source_platform=r.source_platform[:50],
            total_experience=r.total_experience,
            company_id=cid,
            consent_status=body.consent_status,
            consent_at=now if body.consent_status else None,
            consent_source="imported" if body.consent_status else None,
        )
        session.add(c)
        if email:
            by_email[email] = c
        created += 1

    await session.commit()
    return {"created": created, "updated": updated, "skipped": skipped}
