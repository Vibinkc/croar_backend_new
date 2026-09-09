"""Customization — custom fields, tags, dashboard widgets, naming and resume settings.

Everything on Manatal's Customization and Resumes screens that Croar did not have. One router
because they share one idea: account-level settings that change how records look rather than
what they contain.

The custom-field half is the substantial part, and it has two sides:

  * `/fields` DEFINES fields. That is the admin screen.
  * `/values` FILLS them in. That is what makes the first one worth anything — a field you can
    define but never set is a settings page that configures nothing, and shipping only the first
    half is the most common way this feature gets faked.

Validation happens on write, not on read. A select field rejects a value that is not one of its
options, a number rejects text, a required field rejects blank. Doing it on read would mean
every consumer re-implements it and they will disagree.
"""

import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm.attributes import flag_modified

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.company import Company
from app.models.enterprise.customization import (
    CUSTOM_FIELD_ENTITIES,
    CUSTOM_FIELD_TYPES,
    TAG_ENTITIES,
    CustomFieldDefinition,
    CustomFieldValue,
    Tag,
    tag_assignments,
)
from app.models.shared.constants import ModuleScope, PermissionAction

router = APIRouter(prefix="/customization", tags=["Customization"])

Entity = Literal["candidate", "job", "department", "guest", "match"]

# The dashboard as it ships. Customising it starts from this, so a company that never touches
# the screen and one that resets it end up in exactly the same place.
DEFAULT_WIDGETS: dict[str, list[str]] = {
    "left": ["pipeline", "recent_activity", "my_jobs"],
    "right": ["hiring_funnel", "upcoming_interviews", "sourcing_credits"],
    "hidden": [],
}
KNOWN_WIDGETS = {
    "pipeline",
    "recent_activity",
    "my_jobs",
    "hiring_funnel",
    "upcoming_interviews",
    "sourcing_credits",
    "top_performers",
    "offers_out",
}

# Which of the three resume views opens first. Croar stores and shows the original CV; branded
# adds a header and watermark; custom re-renders parsed data onto a template.
RESUME_TABS = ("original", "branded", "custom")

TAG_COLOURS = ("#1976D2", "#00897B", "#7B1FA2", "#C2185B", "#EF6C00", "#455A64", "#2E7D32", "#5D4037")


def _company_id(user: object) -> Any:
    cid = getattr(user, "company_id", None)
    if not cid:
        raise HTTPException(status_code=404, detail="No company on this account.")
    return cid


def _now() -> datetime:
    """Naive UTC — these columns are TIMESTAMP WITHOUT TIME ZONE."""
    return datetime.now(UTC).replace(tzinfo=None)


def _slug(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", label.strip().lower()).strip("_")
    return (s or "field")[:60]


def _def_out(d: CustomFieldDefinition) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "entity": d.entity,
        "key": d.key,
        "label": d.label,
        "field_type": d.field_type,
        "options": d.options or [],
        "help_text": d.help_text,
        "required": d.required,
        "show_on_create": d.show_on_create,
        "position": d.position,
    }


# ───────────────────────────── field definitions ─────────────────────────────


class FieldIn(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    field_type: Literal[
        "text", "textarea", "number", "date", "select", "multiselect", "checkbox", "url", "email", "phone"
    ] = "text"
    options: list[str] = Field(default_factory=list, max_length=100)
    help_text: str | None = None
    required: bool = False
    show_on_create: bool = True


@router.get("/fields")
async def list_fields(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.read))
    ],
    entity: Entity | None = None,
) -> dict[str, Any]:
    """Field definitions, in display order. Omit `entity` to get every record type at once."""
    cid = _company_id(current_user)
    where = [CustomFieldDefinition.company_id == cid, CustomFieldDefinition.deleted_at.is_(None)]
    if entity:
        where.append(CustomFieldDefinition.entity == entity)
    rows = (
        (
            await session.execute(
                select(CustomFieldDefinition)
                .where(*where)
                .order_by(
                    CustomFieldDefinition.entity, CustomFieldDefinition.position, CustomFieldDefinition.label
                )
            )
        )
        .scalars()
        .all()
    )
    out: dict[str, list[dict[str, Any]]] = {e: [] for e in CUSTOM_FIELD_ENTITIES}
    for d in rows:
        out.setdefault(d.entity, []).append(_def_out(d))
    return {
        "entities": list(CUSTOM_FIELD_ENTITIES),
        "types": list(CUSTOM_FIELD_TYPES),
        "fields": out,
        "counts": {e: len(v) for e, v in out.items()},
    }


@router.post("/fields", status_code=201)
async def create_field(
    body: FieldIn,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.create))
    ],
    entity: Entity = Query(...),
) -> dict[str, Any]:
    cid = _company_id(current_user)
    if body.field_type in ("select", "multiselect") and not body.options:
        raise HTTPException(status_code=422, detail="A choice field needs at least one option.")

    key = _slug(body.label)
    clash = (
        await session.execute(
            select(CustomFieldDefinition).where(
                CustomFieldDefinition.company_id == cid,
                CustomFieldDefinition.entity == entity,
                CustomFieldDefinition.key == key,
                CustomFieldDefinition.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if clash:
        raise HTTPException(status_code=409, detail=f'There is already a field called "{body.label}".')

    last = (
        await session.execute(
            select(func.coalesce(func.max(CustomFieldDefinition.position), -1)).where(
                CustomFieldDefinition.company_id == cid, CustomFieldDefinition.entity == entity
            )
        )
    ).scalar_one()

    d = CustomFieldDefinition(
        entity=entity,
        key=key,
        label=body.label.strip(),
        field_type=body.field_type,
        options=[o.strip() for o in body.options if o.strip()] or None,
        help_text=(body.help_text or "").strip() or None,
        required=body.required,
        show_on_create=body.show_on_create,
        position=last + 1,
        company_id=cid,
    )
    session.add(d)
    await session.commit()
    await session.refresh(d)
    return _def_out(d)


@router.patch("/fields/{field_id}")
async def update_field(
    field_id: uuid.UUID,
    body: FieldIn,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.update))
    ],
) -> dict[str, Any]:
    """Edit a field. The key never changes.

    The label is what people read; the key is what stored values and any API caller point at.
    Renaming "Notice period" to "Availability" must not orphan every value already recorded
    against it, so the key stays exactly as it was created.
    """
    cid = _company_id(current_user)
    d = (
        await session.execute(
            select(CustomFieldDefinition).where(
                CustomFieldDefinition.id == field_id,
                CustomFieldDefinition.company_id == cid,
                CustomFieldDefinition.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if d is None:
        raise HTTPException(status_code=404, detail="Field not found")
    if body.field_type in ("select", "multiselect") and not body.options:
        raise HTTPException(status_code=422, detail="A choice field needs at least one option.")

    d.label = body.label.strip()
    d.field_type = body.field_type
    d.options = [o.strip() for o in body.options if o.strip()] or None
    d.help_text = (body.help_text or "").strip() or None
    d.required = body.required
    d.show_on_create = body.show_on_create
    await session.commit()
    await session.refresh(d)
    return _def_out(d)


@router.delete("/fields/{field_id}", status_code=204)
async def delete_field(
    field_id: uuid.UUID,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.delete))
    ],
) -> None:
    """Soft delete. The values stay, so a field removed by mistake comes back with its data."""
    cid = _company_id(current_user)
    d = (
        await session.execute(
            select(CustomFieldDefinition).where(
                CustomFieldDefinition.id == field_id,
                CustomFieldDefinition.company_id == cid,
                CustomFieldDefinition.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if d is None:
        raise HTTPException(status_code=404, detail="Field not found")
    d.deleted_at = _now()
    await session.commit()


class ReorderIn(BaseModel):
    field_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


@router.put("/fields/reorder")
async def reorder_fields(
    body: ReorderIn,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.update))
    ],
    entity: Entity = Query(...),
) -> dict[str, Any]:
    """Set display order from the given sequence. Ids not in it keep their place at the end."""
    cid = _company_id(current_user)
    rows = (
        (
            await session.execute(
                select(CustomFieldDefinition).where(
                    CustomFieldDefinition.company_id == cid,
                    CustomFieldDefinition.entity == entity,
                    CustomFieldDefinition.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    order = {fid: i for i, fid in enumerate(body.field_ids)}
    for d in rows:
        if d.id in order:
            d.position = order[d.id]
    await session.commit()
    return {"reordered": sum(1 for d in rows if d.id in order)}


# ──────────────────────────────── values ─────────────────────────────────────


def _coerce(d: CustomFieldDefinition, raw: Any) -> Any:
    """Turn what the client sent into what this field is allowed to hold, or refuse it.

    Refusing on write rather than sanitising on read is deliberate: every consumer would
    otherwise re-implement the check, and they would disagree about the edge cases.
    """
    if raw is None or raw == "":
        if d.required:
            raise HTTPException(status_code=422, detail=f"{d.label} is required.")
        return None

    t = d.field_type
    if t == "checkbox":
        return bool(raw)
    if t == "number":
        try:
            return float(raw) if not float(raw).is_integer() else int(float(raw))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail=f"{d.label} must be a number.") from None
    if t == "select":
        if raw not in (d.options or []):
            raise HTTPException(status_code=422, detail=f'"{raw}" is not one of the choices for {d.label}.')
        return raw
    if t == "multiselect":
        if not isinstance(raw, list):
            raise HTTPException(status_code=422, detail=f"{d.label} takes a list of choices.")
        bad = [v for v in raw if v not in (d.options or [])]
        if bad:
            raise HTTPException(
                status_code=422, detail=f"{', '.join(map(str, bad))} is not a choice for {d.label}."
            )
        return raw
    if t == "email" and "@" not in str(raw):
        raise HTTPException(status_code=422, detail=f"{d.label} must be an email address.")
    if t == "url" and not str(raw).startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail=f"{d.label} must start with http:// or https://.")
    if t == "date":
        try:
            datetime.fromisoformat(str(raw)[:10])
        except ValueError:
            raise HTTPException(status_code=422, detail=f"{d.label} must be a date (YYYY-MM-DD).") from None
    return str(raw)[:5000]


class ValuesIn(BaseModel):
    """Keyed by field key, not by id — a caller filling in a candidate knows "notice_period",
    not a uuid, and an import file certainly does."""

    values: dict[str, Any]


@router.get("/values/{entity}/{record_id}")
async def get_values(
    entity: Entity,
    record_id: uuid.UUID,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
) -> dict[str, Any]:
    """This record's custom fields: every definition, with its value where one is set.

    Definitions with no value are still returned, empty. A caller rendering a form needs the
    fields that have never been filled in more than it needs the ones that have.
    """
    cid = _company_id(current_user)
    defs = (
        (
            await session.execute(
                select(CustomFieldDefinition)
                .where(
                    CustomFieldDefinition.company_id == cid,
                    CustomFieldDefinition.entity == entity,
                    CustomFieldDefinition.deleted_at.is_(None),
                )
                .order_by(CustomFieldDefinition.position, CustomFieldDefinition.label)
            )
        )
        .scalars()
        .all()
    )
    if not defs:
        return {"fields": []}

    values = {
        v.definition_id: v.value
        for v in (
            await session.execute(
                select(CustomFieldValue).where(
                    CustomFieldValue.company_id == cid,
                    CustomFieldValue.record_id == record_id,
                    CustomFieldValue.definition_id.in_([d.id for d in defs]),
                )
            )
        )
        .scalars()
        .all()
    }
    return {"fields": [{**_def_out(d), "value": values.get(d.id)} for d in defs]}


@router.put("/values/{entity}/{record_id}")
async def set_values(
    entity: Entity,
    record_id: uuid.UUID,
    body: ValuesIn,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.update))
    ],
) -> dict[str, Any]:
    """Write the given fields. Keys not sent are left alone; a key sent as null clears it."""
    cid = _company_id(current_user)
    defs = {
        d.key: d
        for d in (
            await session.execute(
                select(CustomFieldDefinition).where(
                    CustomFieldDefinition.company_id == cid,
                    CustomFieldDefinition.entity == entity,
                    CustomFieldDefinition.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    }
    unknown = [k for k in body.values if k not in defs]
    if unknown:
        # Named rather than ignored: a typo in a key is otherwise a value that silently never
        # arrives, and the caller has no way to tell that from an empty field.
        raise HTTPException(status_code=422, detail=f"No such field: {', '.join(unknown)}")

    existing = {
        v.definition_id: v
        for v in (
            await session.execute(
                select(CustomFieldValue).where(
                    CustomFieldValue.company_id == cid, CustomFieldValue.record_id == record_id
                )
            )
        )
        .scalars()
        .all()
    }

    written = 0
    for key, raw in body.values.items():
        d = defs[key]
        val = _coerce(d, raw)
        row = existing.get(d.id)
        if row is None:
            session.add(CustomFieldValue(definition_id=d.id, record_id=record_id, value=val, company_id=cid))
        else:
            row.value = val
        written += 1

    await session.commit()
    return {"written": written}


# ────────────────────────────────── tags ─────────────────────────────────────


class TagIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    colour: str | None = None


@router.get("/tags")
async def list_tags(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.read))
    ],
    entity: Literal["candidate", "job"] = "candidate",
) -> dict[str, Any]:
    """Tags, each with how many records currently carry it.

    The count is the useful part: it is how you tell a tag worth keeping from one somebody
    created once and never used again.
    """
    cid = _company_id(current_user)
    tags = (
        (
            await session.execute(
                select(Tag)
                .where(Tag.company_id == cid, Tag.entity == entity, Tag.deleted_at.is_(None))
                .order_by(Tag.name)
            )
        )
        .scalars()
        .all()
    )
    counts = dict(
        (
            await session.execute(
                select(tag_assignments.c.tag_id, func.count())
                .where(tag_assignments.c.tag_id.in_([t.id for t in tags]))
                .group_by(tag_assignments.c.tag_id)
            )
        ).all()
        if tags
        else []
    )
    return {
        "entities": list(TAG_ENTITIES),
        "colours": list(TAG_COLOURS),
        "tags": [
            {
                "id": str(t.id),
                "name": t.name,
                "colour": t.colour or TAG_COLOURS[0],
                "used_by": counts.get(t.id, 0),
            }
            for t in tags
        ],
    }


@router.post("/tags", status_code=201)
async def create_tag(
    body: TagIn,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.create))
    ],
    entity: Literal["candidate", "job"] = "candidate",
) -> dict[str, Any]:
    cid = _company_id(current_user)
    name = body.name.strip()
    clash = (
        await session.execute(
            select(Tag).where(
                Tag.company_id == cid,
                Tag.entity == entity,
                func.lower(Tag.name) == name.lower(),
                Tag.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if clash:
        raise HTTPException(status_code=409, detail=f'There is already a tag called "{name}".')
    t = Tag(
        entity=entity,
        name=name,
        colour=body.colour if body.colour in TAG_COLOURS else TAG_COLOURS[0],
        company_id=cid,
    )
    session.add(t)
    await session.commit()
    await session.refresh(t)
    return {"id": str(t.id), "name": t.name, "colour": t.colour, "used_by": 0}


@router.patch("/tags/{tag_id}")
async def update_tag(
    tag_id: uuid.UUID,
    body: TagIn,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.update))
    ],
) -> dict[str, Any]:
    """Rename or recolour. Renaming keeps every record that carries it — the tag is the row,
    not the word, so this is a rename and not a move."""
    cid = _company_id(current_user)
    t = (
        await session.execute(
            select(Tag).where(Tag.id == tag_id, Tag.company_id == cid, Tag.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    t.name = body.name.strip()
    if body.colour in TAG_COLOURS:
        t.colour = body.colour
    await session.commit()
    return {"id": str(t.id), "name": t.name, "colour": t.colour}


@router.delete("/tags/{tag_id}", status_code=204)
async def delete_tag(
    tag_id: uuid.UUID,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.delete))
    ],
) -> None:
    """Delete a tag and take it off everything at once.

    Unlike a field, the assignments go too. A tag is only ever a label — keeping dangling
    assignment rows for a tag nobody can see again buys nothing and makes the counts lie.
    """
    cid = _company_id(current_user)
    t = (
        await session.execute(
            select(Tag).where(Tag.id == tag_id, Tag.company_id == cid, Tag.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    await session.execute(delete(tag_assignments).where(tag_assignments.c.tag_id == t.id))
    t.deleted_at = _now()
    await session.commit()


class TagRecordIn(BaseModel):
    tag_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)


@router.put("/tags/on/{entity}/{record_id}")
async def set_record_tags(
    entity: Literal["candidate", "job"],
    record_id: uuid.UUID,
    body: TagRecordIn,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.update))
    ],
) -> dict[str, Any]:
    """Replace the tags on one record with exactly this set."""
    cid = _company_id(current_user)
    valid = (
        (
            await session.execute(
                select(Tag.id).where(
                    Tag.id.in_(body.tag_ids),
                    # Scoped on the way in: a tag id from another company must not become
                    # attachable, or the tag list on a record could name someone else's labels.
                    Tag.company_id == cid,
                    Tag.entity == entity,
                    Tag.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
        if body.tag_ids
        else []
    )
    mine = (
        (
            await session.execute(
                select(Tag.id).where(Tag.company_id == cid, Tag.entity == entity, Tag.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    await session.execute(
        delete(tag_assignments).where(
            tag_assignments.c.record_id == record_id, tag_assignments.c.tag_id.in_(mine)
        )
    )
    for tid in valid:
        await session.execute(tag_assignments.insert().values(tag_id=tid, record_id=record_id))
    await session.commit()
    return {"tags": [str(t) for t in valid], "ignored": len(set(body.tag_ids)) - len(valid)}


@router.get("/tags/on/{entity}/{record_id}")
async def get_record_tags(
    entity: Literal["candidate", "job"],
    record_id: uuid.UUID,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
) -> dict[str, Any]:
    cid = _company_id(current_user)
    rows = (
        (
            await session.execute(
                select(Tag)
                .join(tag_assignments, tag_assignments.c.tag_id == Tag.id)
                .where(
                    tag_assignments.c.record_id == record_id,
                    Tag.company_id == cid,
                    Tag.entity == entity,
                    Tag.deleted_at.is_(None),
                )
                .order_by(Tag.name)
            )
        )
        .scalars()
        .all()
    )
    return {"tags": [{"id": str(t.id), "name": t.name, "colour": t.colour} for t in rows]}


# ─────────────────── settings that live on the company record ────────────────


async def _company(session: DBSessionDep, cid: Any) -> Company:
    c = (await session.execute(select(Company).where(Company.id == cid))).scalar_one_or_none()
    if c is None:
        raise HTTPException(status_code=404, detail="Company not found")
    if c.config is None:
        c.config = {}
    return c


def _write_config(c: Company, key: str, value: Any) -> None:
    """Set one key inside the config blob.

    `flag_modified` is not optional here: SQLAlchemy compares JSONB by identity, so mutating the
    dict in place leaves the session thinking nothing changed and the commit writes nothing.
    """
    cfg = dict(c.config or {})
    cfg[key] = value
    c.config = cfg
    flag_modified(c, "config")


class WidgetsIn(BaseModel):
    left: list[str] = Field(default_factory=list, max_length=20)
    right: list[str] = Field(default_factory=list, max_length=20)
    hidden: list[str] = Field(default_factory=list, max_length=20)


@router.get("/dashboard-widgets")
async def get_widgets(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.read))
    ],
) -> dict[str, Any]:
    cid = _company_id(current_user)
    c = await _company(session, cid)
    saved = (c.config or {}).get("dashboard_widgets")
    return {
        "layout": saved or DEFAULT_WIDGETS,
        "available": sorted(KNOWN_WIDGETS),
        "is_default": saved is None,
    }


@router.put("/dashboard-widgets")
async def set_widgets(
    body: WidgetsIn,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.update))
    ],
) -> dict[str, Any]:
    """Save the layout, refusing one that would lose or duplicate a widget.

    A widget in two columns at once, or missing from all three, would render as either a
    duplicate or a silent disappearance — and the person who did it would have no way to tell
    which. Cheaper to refuse than to explain.
    """
    cid = _company_id(current_user)
    placed = body.left + body.right + body.hidden
    unknown = [w for w in placed if w not in KNOWN_WIDGETS]
    if unknown:
        raise HTTPException(status_code=422, detail=f"No such widget: {', '.join(unknown)}")
    if len(placed) != len(set(placed)):
        raise HTTPException(status_code=422, detail="A widget cannot be in two places at once.")
    missing = KNOWN_WIDGETS - set(placed)
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Every widget must be placed or hidden. Missing: {', '.join(sorted(missing))}",
        )

    c = await _company(session, cid)
    _write_config(c, "dashboard_widgets", {"left": body.left, "right": body.right, "hidden": body.hidden})
    await session.commit()
    return {"layout": {"left": body.left, "right": body.right, "hidden": body.hidden}}


@router.post("/dashboard-widgets/reset")
async def reset_widgets(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.update))
    ],
) -> dict[str, Any]:
    cid = _company_id(current_user)
    c = await _company(session, cid)
    _write_config(c, "dashboard_widgets", None)
    await session.commit()
    return {"layout": DEFAULT_WIDGETS, "is_default": True}


class NamingIn(BaseModel):
    """What this account calls a department.

    Manatal calls the concept an "organization" and lets you relabel it, because the same field
    means "client" to an agency and "department" to an in-house team. It is one label, and
    getting it wrong makes every screen read slightly foreign.
    """

    singular: str = Field(min_length=1, max_length=40)
    plural: str = Field(min_length=1, max_length=40)


@router.get("/naming")
async def get_naming(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.read))
    ],
) -> dict[str, Any]:
    cid = _company_id(current_user)
    c = await _company(session, cid)
    saved = (c.config or {}).get("department_naming") or {}
    return {
        "singular": saved.get("singular", "department"),
        "plural": saved.get("plural", "departments"),
        "presets": [
            {"singular": "department", "plural": "departments"},
            {"singular": "client", "plural": "clients"},
            {"singular": "business unit", "plural": "business units"},
            {"singular": "team", "plural": "teams"},
        ],
    }


@router.put("/naming")
async def set_naming(
    body: NamingIn,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.update))
    ],
) -> dict[str, Any]:
    cid = _company_id(current_user)
    c = await _company(session, cid)
    _write_config(c, "department_naming", {"singular": body.singular.strip(), "plural": body.plural.strip()})
    await session.commit()
    return {"singular": body.singular.strip(), "plural": body.plural.strip()}


class ResumeSettingsIn(BaseModel):
    default_tab: Literal["original", "branded", "custom"] = "original"
    watermark_enabled: bool = False
    watermark_text: str | None = Field(default=None, max_length=60)
    header_logo_url: str | None = Field(default=None, max_length=500)
    org_name: str | None = Field(default=None, max_length=200)
    org_email: str | None = Field(default=None, max_length=255)
    org_website: str | None = Field(default=None, max_length=255)
    org_address: str | None = Field(default=None, max_length=500)
    hide_contact_details: bool = False


@router.get("/resumes")
async def get_resume_settings(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.read))
    ],
) -> dict[str, Any]:
    """Settings for the three resume views, and which one opens first."""
    cid = _company_id(current_user)
    c = await _company(session, cid)
    saved = (c.config or {}).get("resume_settings") or {}
    return {
        "tabs": list(RESUME_TABS),
        "default_tab": saved.get("default_tab", "original"),
        "watermark_enabled": saved.get("watermark_enabled", False),
        # Falls back to the company name: a watermark that says nothing is worse than no
        # watermark, and the account name is the answer nine times out of ten.
        "watermark_text": saved.get("watermark_text") or c.name,
        "header_logo_url": saved.get("header_logo_url") or c.logo_url,
        "org_name": saved.get("org_name") or c.name,
        "org_email": saved.get("org_email") or c.contact_email,
        "org_website": saved.get("org_website"),
        "org_address": saved.get("org_address"),
        # The reason an agency wants a branded resume at all: send the client the profile
        # without handing over the means to go round you.
        "hide_contact_details": saved.get("hide_contact_details", False),
    }


@router.put("/resumes")
async def set_resume_settings(
    body: ResumeSettingsIn,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.update))
    ],
) -> dict[str, Any]:
    cid = _company_id(current_user)
    c = await _company(session, cid)
    _write_config(c, "resume_settings", body.model_dump())
    await session.commit()
    return body.model_dump()
