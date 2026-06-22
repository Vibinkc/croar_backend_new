import re
import uuid
from collections import deque

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy import select

from app.models.enterprise.company import Company
from app.models.payroll import SalaryStructure, SalaryTemplate
from app.payroll.constants import Permission
from app.payroll.deps import DBSessionDep, get_current_company_id, require_permission
from app.schemas.enterprise.payroll.settings import (
    OrganizationOut,
    OrganizationUpdate,
    PayslipDocScanOut,
    PayslipFieldOption,
    PayslipMappingApply,
    PayslipSettings,
    PayslipSettingsOut,
    PayslipSettingsUpdate,
    StatutoryConfig,
    StatutoryConfigUpdate,
)
from app.services.payroll import docx_service

# Max size for an uploaded payslip .docx template.
_MAX_DOC_BYTES = 5 * 1024 * 1024

router = APIRouter(prefix="/api/v1/enterprise/settings", tags=["settings"])


async def _load_company(db: DBSessionDep, company_id: uuid.UUID) -> Company:
    company = (
        await db.execute(select(Company).where(Company.id == company_id, Company.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return company


@router.get("/organization", response_model=OrganizationOut)
async def get_organization(
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> Company:
    """The signed-in user's organisation (Settings → Organisation Profile)."""
    return await _load_company(db, company_id)


@router.put("/organization", response_model=OrganizationOut)
async def update_organization(
    payload: OrganizationUpdate,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.USERS_MANAGE)),
) -> Company:
    """Edit organisation profile. Admin-only (users:manage). Partial update —
    only the fields present in the request are changed."""
    company = await _load_company(db, company_id)
    fields = payload.model_dump(exclude_unset=True)
    non_nullable = {"name", "currency", "country"}
    for key, value in fields.items():
        if isinstance(value, str):
            value = value.strip()
            # Optional fields: blank -> NULL. Required fields keep their value.
            if value == "" and key not in non_nullable:
                value = None
        if key in ("pan", "tan") and value:
            value = value.upper()
        setattr(company, key, value)
    try:
        await db.commit()
        await db.refresh(company)
    except Exception:
        await db.rollback()
        raise
    return company


# ---------------------------------------------------------------------------
# Payslip template (Settings → Payslip)
# ---------------------------------------------------------------------------
def _payslip_out(company: Company) -> PayslipSettingsOut:
    settings = PayslipSettings.from_stored(company.payslip_settings)
    return PayslipSettingsOut(
        **settings.model_dump(),
        company_name=company.name,
        has_doc_template=company.payslip_doc_template is not None,
        doc_filename=company.payslip_doc_filename,
        doc_mapped=company.payslip_doc_original is not None and bool(company.payslip_doc_mapping),
        doc_has_tokens=company.payslip_doc_template is not None
        and docx_service.has_jinja_tokens(company.payslip_doc_template),
    )


@router.get("/payslip", response_model=PayslipSettingsOut)
async def get_payslip_settings(
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> PayslipSettingsOut:
    """The company's payslip template (branding + section toggles)."""
    company = await _load_company(db, company_id)
    return _payslip_out(company)


@router.put("/payslip", response_model=PayslipSettingsOut)
async def update_payslip_settings(
    payload: PayslipSettingsUpdate,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.USERS_MANAGE)),
) -> PayslipSettingsOut:
    """Edit the payslip template. Admin-only (users:manage). Partial update —
    only the fields present in the request are changed."""
    company = await _load_company(db, company_id)
    changes = payload.model_dump(exclude_unset=True)
    # Blank text fields -> None ("use the built-in default").
    for key in ("display_name", "logo_url", "accent_color", "footer_note"):
        if key in changes and isinstance(changes[key], str) and changes[key].strip() == "":
            changes[key] = None
    # Merge over the stored blob, then re-validate through the canonical model so
    # the persisted JSON always has a clean, fully-defaulted shape.
    merged = {**(company.payslip_settings or {}), **changes}
    company.payslip_settings = PayslipSettings(**merged).model_dump()
    try:
        await db.commit()
        await db.refresh(company)
    except Exception:
        await db.rollback()
        raise
    return _payslip_out(company)


_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.get("/payslip/document/sample")
async def download_sample_payslip_template(
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> Response:
    """A ready-to-use .docx template with correct tokens already placed."""
    return Response(
        content=docx_service.sample_payslip_template(),
        media_type=_DOCX_MIME,
        headers={"Content-Disposition": 'attachment; filename="payslip-template-sample.docx"'},
    )


@router.put("/payslip/document", response_model=PayslipSettingsOut)
async def upload_payslip_document(
    db: DBSessionDep,
    file: UploadFile = File(...),
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.USERS_MANAGE)),
) -> PayslipSettingsOut:
    """Upload a .docx payslip template (Jinja/docxtpl tokens). Admin-only.

    Enable it via the ``use_doc_template`` flag (PUT /payslip) to have payslips
    generated from this document."""
    data = await file.read()
    if len(data) > _MAX_DOC_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Template too large (max 5 MB)."
        )
    if not docx_service.looks_like_docx(data):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Please upload a Word .docx document."
        )
    company = await _load_company(db, company_id)
    company.payslip_doc_template = data
    company.payslip_doc_filename = (file.filename or "payslip-template.docx")[:255]
    # This is a direct (already-tokenised) upload — drop any wizard state.
    company.payslip_doc_original = None
    company.payslip_doc_mapping = None
    try:
        await db.commit()
        await db.refresh(company)
    except Exception:
        await db.rollback()
        raise
    return _payslip_out(company)


@router.delete("/payslip/document", response_model=PayslipSettingsOut)
async def delete_payslip_document(
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.USERS_MANAGE)),
) -> PayslipSettingsOut:
    """Remove the uploaded payslip template and disable its use. Admin-only."""
    company = await _load_company(db, company_id)
    company.payslip_doc_template = None
    company.payslip_doc_filename = None
    company.payslip_doc_original = None
    company.payslip_doc_mapping = None
    # Don't leave the flag pointing at a now-missing template.
    merged = {**(company.payslip_settings or {}), "use_doc_template": False}
    company.payslip_settings = PayslipSettings(**merged).model_dump()
    try:
        await db.commit()
        await db.refresh(company)
    except Exception:
        await db.rollback()
        raise
    return _payslip_out(company)


# ---- Smart mapping wizard --------------------------------------------------
def _normalize_label(s: str) -> str:
    """Mirror docx_service._normalize so component labels match detected ones."""
    s = s.lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


async def _company_components(
    db: DBSessionDep, company_id: uuid.UUID
) -> tuple[list[PayslipFieldOption], dict[str, str]]:
    """The company's own salary-component codes, as extra mappable fields +
    auto-detect synonyms. So a template row like "Performance Bonus" can be
    mapped to that exact component (token ``amount.<CODE>``) and even auto-
    suggested when its label matches."""
    rows = (
        await db.execute(
            select(SalaryStructure.components, SalaryStructure.default_deductions).where(
                SalaryStructure.company_id == company_id
            )
        )
    ).all()
    trows = (
        await db.execute(
            select(SalaryTemplate.components, SalaryTemplate.default_deductions).where(
                SalaryTemplate.company_id == company_id
            )
        )
    ).all()

    # code -> friendly label (first non-empty label wins).
    seen: dict[str, str] = {}
    for comps, deds in [*rows, *trows]:
        for line in [*(comps or []), *(deds or [])]:
            code = (line.get("code") or "").strip()
            if not code:
                continue
            label = (line.get("label") or code).strip()
            seen.setdefault(code, label)

    # Skip codes already covered by the static catalogue (BASIC, HRA, PF, …).
    static_keys = {f["key"] for f in docx_service.FIELD_CATALOG}
    fields: list[PayslipFieldOption] = []
    synonyms: dict[str, str] = {}
    for code, label in sorted(seen.items()):
        token = f"amount.{code}"
        synonyms[_normalize_label(label)] = token
        synonyms[_normalize_label(code)] = token
        if token not in static_keys:
            fields.append(
                PayslipFieldOption(group="Your salary components", key=token, label=f"{label} ({code})")
            )
    return fields, synonyms


def _field_catalog(extra: list[PayslipFieldOption] | None = None) -> list[PayslipFieldOption]:
    base = [PayslipFieldOption(**f) for f in docx_service.FIELD_CATALOG]
    return base + (extra or [])


def _prefill_from_saved(slots: list, saved: dict) -> None:
    """Pre-fill each detected slot with the admin's previously saved choice.

    The saved mapping may be the old flat ``{index: token}`` shape or the new
    ``{index: {token, label}}`` shape. When labels are present we re-attach by
    LABEL (matching repeated labels in order), so saved choices survive changes
    to slot ordering/detection; otherwise we fall back to matching by index."""
    items: list[tuple[str | None, str]] = []  # (normalized label | None, token)
    for k in sorted(saved, key=lambda x: int(x) if str(x).isdigit() else 0):
        v = saved[k]
        if isinstance(v, dict):
            items.append((_normalize_label(v.get("label") or ""), v.get("token") or ""))
        else:
            items.append((None, v))

    if any(lbl for lbl, _ in items):
        queues: dict[str, deque] = {}
        for lbl, tok in items:
            if lbl:
                queues.setdefault(lbl, deque()).append(tok)
        for slot in slots:
            q = queues.get(_normalize_label(slot.label))
            if q:
                slot.suggested_token = q.popleft()
    else:  # legacy positional format
        for slot in slots:
            if str(slot.index) in saved:
                slot.suggested_token = saved[str(slot.index)]


def _scan_to_out(
    filename: str, data: bytes, extra_fields: list[PayslipFieldOption], synonyms: dict[str, str]
) -> PayslipDocScanOut:
    slots = docx_service.scan_docx_fields(data, extra_synonyms=synonyms)
    return PayslipDocScanOut(filename=filename, slots=slots, fields=_field_catalog(extra_fields))


@router.post("/payslip/document/scan", response_model=PayslipDocScanOut)
async def scan_payslip_document(
    db: DBSessionDep,
    file: UploadFile = File(...),
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.USERS_MANAGE)),
) -> PayslipDocScanOut:
    """Smart-mapping step 1: upload your existing (token-free) payslip .docx.

    We store it as the original, scan it for value-slots, and return the
    detected slots + the field catalogue. Nothing is generated yet — confirm the
    mapping via PUT /payslip/document/mapping to produce the active template."""
    data = await file.read()
    if len(data) > _MAX_DOC_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Template too large (max 5 MB)."
        )
    if not docx_service.looks_like_docx(data):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Please upload a Word .docx document."
        )
    extra_fields, synonyms = await _company_components(db, company_id)
    try:
        out = _scan_to_out((file.filename or "payslip-template.docx")[:255], data, extra_fields, synonyms)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not read this .docx. Make sure it's a valid Word file.",
        )
    company = await _load_company(db, company_id)
    company.payslip_doc_original = data
    company.payslip_doc_filename = out.filename
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return out


@router.get("/payslip/document/mapping", response_model=PayslipDocScanOut)
async def get_payslip_mapping(
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.USERS_MANAGE)),
) -> PayslipDocScanOut:
    """Re-open the wizard for an already-scanned template: re-scan the stored
    original and pre-fill each slot with the previously saved mapping (so the
    admin can adjust it without re-uploading)."""
    company = await _load_company(db, company_id)
    if not company.payslip_doc_original:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No mappable template — upload one via the wizard first.",
        )
    extra_fields, synonyms = await _company_components(db, company_id)
    out = _scan_to_out(
        company.payslip_doc_filename or "payslip-template.docx",
        company.payslip_doc_original,
        extra_fields,
        synonyms,
    )
    saved = company.payslip_doc_mapping or {}
    if saved:
        _prefill_from_saved(out.slots, saved)
    return out


@router.put("/payslip/document/mapping", response_model=PayslipSettingsOut)
async def apply_payslip_mapping(
    payload: PayslipMappingApply,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.USERS_MANAGE)),
) -> PayslipSettingsOut:
    """Smart-mapping step 2: inject the confirmed tokens into the stored original
    and make the result the active payslip template (and enable its use)."""
    company = await _load_company(db, company_id)
    if not company.payslip_doc_original:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No scanned template to map — upload one via the wizard first.",
        )
    # JSON keys are strings; keep only real (non-blank) token choices.
    clean = {k: v for k, v in payload.mapping.items() if v}
    if not clean:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Map at least one field before saving."
        )
    by_index: dict[int, str] = {}
    for k, v in clean.items():
        try:
            by_index[int(k)] = v
        except (TypeError, ValueError):
            continue
    try:
        filled = docx_service.apply_field_mapping(company.payslip_doc_original, by_index)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not apply the mapping to this document.",
        )
    company.payslip_doc_template = filled
    # Persist the mapping WITH each slot's label, so re-opening "Edit mapping"
    # can re-attach the choices by label even if detection/order changes later.
    _, synonyms = await _company_components(db, company_id)
    scan_slots = docx_service.scan_docx_fields(company.payslip_doc_original, extra_synonyms=synonyms)
    label_by_index = {s["index"]: s["label"] for s in scan_slots}
    company.payslip_doc_mapping = {
        str(i): {"token": tok, "label": label_by_index.get(i, "")} for i, tok in by_index.items()
    }
    # Turn the doc template on so payslips immediately use the mapped template.
    merged = {**(company.payslip_settings or {}), "use_doc_template": True}
    company.payslip_settings = PayslipSettings(**merged).model_dump()
    try:
        await db.commit()
        await db.refresh(company)
    except Exception:
        await db.rollback()
        raise
    return _payslip_out(company)


# ---------------------------------------------------------------------------
# Statutory configuration (Settings → Statutory Compliance)
# ---------------------------------------------------------------------------
@router.get("/statutory", response_model=StatutoryConfig)
async def get_statutory_config(
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.PAYROLL_READ)),
) -> StatutoryConfig:
    """The company's statutory rates/thresholds (overrides + code defaults)."""
    company = await _load_company(db, company_id)
    return StatutoryConfig.from_stored(company.statutory_settings)


@router.put("/statutory", response_model=StatutoryConfig)
async def update_statutory_config(
    payload: StatutoryConfigUpdate,
    db: DBSessionDep,
    company_id: uuid.UUID = Depends(get_current_company_id),
    _: object = Depends(require_permission(Permission.USERS_MANAGE)),
) -> StatutoryConfig:
    """Edit statutory rates/thresholds. Admin-only (users:manage). Partial
    update; the result applies to every payroll run, payslip and live preview."""
    company = await _load_company(db, company_id)
    changes = payload.model_dump(exclude_unset=True)
    merged = {**(company.statutory_settings or {}), **changes}
    # Re-validate through the canonical model so the stored JSON is always clean.
    company.statutory_settings = StatutoryConfig(**merged).model_dump()
    try:
        await db.commit()
        await db.refresh(company)
    except Exception:
        await db.rollback()
        raise
    return StatutoryConfig.from_stored(company.statutory_settings)
