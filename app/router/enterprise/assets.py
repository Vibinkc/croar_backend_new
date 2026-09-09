"""Company assets — what you own, who has it, and whether it came back.

Modelled on Oorwin's Assets Management, whose create form was read from their live product:
Asset ID, Type, Asset Name, Category, Brand, Status, License Key, Ownership, Assign To, Email,
Phone, Comments. Croar keeps the same shape and drops the contact fields, because the holder is
an employee record here rather than a name typed twice.

The single rule this module exists to enforce: **one holder at a time**. Assigning an asset that
is already out is refused rather than silently creating a second open assignment, because two
open rows makes "who has it" unanswerable and there is no correct one to pick.

Returning is its own action rather than an edit, and it records the condition. "Returned" alone
lets a smashed screen and a pristine machine settle identically, which is the difference between
a deduction and a thank-you.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.asset import (
    ASSET_CATEGORIES,
    ASSET_OWNERSHIP,
    ASSET_STATUSES,
    RETURN_CONDITIONS,
    Asset,
    AssetAssignment,
)
from app.models.enterprise.employee import Employee
from app.models.shared.constants import ModuleScope, PermissionAction

router = APIRouter(prefix="/assets", tags=["Assets"])


def _company(user: object) -> Any:
    cid = getattr(user, "company_id", None)
    if not cid:
        raise HTTPException(status_code=404, detail="No company on this account.")
    return cid


def _now() -> datetime:
    """Naive UTC — these columns are TIMESTAMP WITHOUT TIME ZONE."""
    return datetime.now(UTC).replace(tzinfo=None)


def _naive(d: datetime | None) -> datetime | None:
    return d.replace(tzinfo=None) if d and d.tzinfo else d


def _employee_name(e: Employee | None) -> str | None:
    if e is None:
        return None
    return " ".join(p for p in (e.first_name, e.last_name) if p).strip() or e.email


def _open_assignment(a: Asset) -> AssetAssignment | None:
    """The one assignment with no return date, if any. There is at most one by construction."""
    return next((x for x in a.assignments if x.returned_at is None), None)


def _asset_out(a: Asset) -> dict[str, Any]:
    open_a = _open_assignment(a)
    return {
        "id": str(a.id),
        "asset_tag": a.asset_tag,
        "name": a.name,
        "category": a.category,
        "brand": a.brand,
        "model": a.model,
        "serial_number": a.serial_number,
        # Present as a boolean rather than the value: whoever is looking at a list of assets does
        # not need the licence key, and a list endpoint that returns credentials puts them in
        # every log and every screenshot of the page.
        "has_licence_key": bool(a.licence_key),
        "ownership": a.ownership,
        "status": a.status,
        "purchased_on": a.purchased_on.isoformat() if a.purchased_on else None,
        "purchase_cost": float(a.purchase_cost) if a.purchase_cost is not None else None,
        "notes": a.notes,
        "held_by": (
            {
                "assignment_id": str(open_a.id),
                "employee_id": str(open_a.employee_id),
                "employee_name": _employee_name(open_a.employee),
                "assigned_at": open_a.assigned_at.isoformat() if open_a.assigned_at else None,
                "due_back_on": open_a.due_back_on.isoformat() if open_a.due_back_on else None,
            }
            if open_a
            else None
        ),
        "times_assigned": len(a.assignments),
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


async def _load(session: DBSessionDep, cid: Any, asset_id: uuid.UUID) -> Asset:
    a = (
        await session.execute(
            select(Asset).where(Asset.id == asset_id, Asset.company_id == cid, Asset.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if a is None:
        # 404 not 403 across tenants — a 403 confirms the id exists.
        raise HTTPException(status_code=404, detail="Asset not found")
    return a


class AssetIn(BaseModel):
    asset_tag: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=200)
    category: Literal[
        "laptop",
        "desktop",
        "monitor",
        "phone",
        "tablet",
        "peripheral",
        "furniture",
        "access_card",
        "software_licence",
        "vehicle",
        "other",
    ] = "other"
    brand: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=100)
    serial_number: str | None = Field(default=None, max_length=120)
    licence_key: str | None = Field(default=None, max_length=200)
    ownership: Literal["company", "leased", "employee"] = "company"
    purchased_on: datetime | None = None
    purchase_cost: float | None = Field(default=None, ge=0)
    notes: str | None = None


@router.get("")
async def list_assets(
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.read))],
    q: str | None = None,
    category: str | None = None,
    status: str | None = None,
    employee_id: uuid.UUID | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
) -> dict[str, Any]:
    """The register, filterable. `employee_id` narrows to what one person is holding now."""
    cid = _company(current_user)
    where = [Asset.company_id == cid, Asset.deleted_at.is_(None)]
    if q and q.strip():
        like = f"%{q.strip()}%"
        where.append(
            or_(
                Asset.name.ilike(like),
                Asset.asset_tag.ilike(like),
                Asset.serial_number.ilike(like),
                Asset.brand.ilike(like),
            )
        )
    if category:
        where.append(Asset.category == category)
    if status:
        where.append(Asset.status == status)
    if employee_id:
        where.append(
            Asset.id.in_(
                select(AssetAssignment.asset_id).where(
                    AssetAssignment.employee_id == employee_id,
                    AssetAssignment.returned_at.is_(None),
                    AssetAssignment.company_id == cid,
                )
            )
        )

    total = (await session.execute(select(func.count()).select_from(Asset).where(*where))).scalar_one()
    rows = (
        (
            await session.execute(
                select(Asset)
                .where(*where)
                .order_by(Asset.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .unique()
        .all()
    )
    return {"total": total, "page": page, "page_size": page_size, "results": [_asset_out(a) for a in rows]}


@router.get("/summary")
async def summary(
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.read))],
) -> dict[str, Any]:
    """Counts by status, plus the number that are overdue back.

    Overdue is the number worth surfacing: it is the only one that implies somebody has to do
    something today.
    """
    cid = _company(current_user)
    by_status = dict(
        (
            await session.execute(
                select(Asset.status, func.count())
                .where(Asset.company_id == cid, Asset.deleted_at.is_(None))
                .group_by(Asset.status)
            )
        ).all()
    )
    overdue = (
        await session.execute(
            select(func.count())
            .select_from(AssetAssignment)
            .where(
                AssetAssignment.company_id == cid,
                AssetAssignment.returned_at.is_(None),
                AssetAssignment.due_back_on.is_not(None),
                AssetAssignment.due_back_on < _now(),
            )
        )
    ).scalar_one()
    return {
        "by_status": {s: by_status.get(s, 0) for s in ASSET_STATUSES},
        "total": sum(by_status.values()),
        "overdue": overdue,
        "categories": list(ASSET_CATEGORIES),
        "ownership": list(ASSET_OWNERSHIP),
        "statuses": list(ASSET_STATUSES),
        "return_conditions": list(RETURN_CONDITIONS),
    }


@router.post("", status_code=201)
async def create_asset(
    body: AssetIn,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.create))
    ],
) -> dict[str, Any]:
    cid = _company(current_user)
    tag = body.asset_tag.strip()
    clash = (
        await session.execute(
            select(Asset).where(
                Asset.company_id == cid,
                func.lower(Asset.asset_tag) == tag.lower(),
                Asset.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if clash:
        raise HTTPException(status_code=409, detail=f'There is already an asset tagged "{tag}".')

    a = Asset(
        asset_tag=tag,
        name=body.name.strip(),
        category=body.category,
        brand=(body.brand or "").strip() or None,
        model=(body.model or "").strip() or None,
        serial_number=(body.serial_number or "").strip() or None,
        licence_key=(body.licence_key or "").strip() or None,
        ownership=body.ownership,
        status="available",
        purchased_on=_naive(body.purchased_on),
        purchase_cost=body.purchase_cost,
        notes=(body.notes or "").strip() or None,
        company_id=cid,
        created_by=getattr(current_user, "id", None),
    )
    session.add(a)
    await session.commit()
    await session.refresh(a)
    return _asset_out(a)


@router.patch("/{asset_id}")
async def update_asset(
    asset_id: uuid.UUID,
    body: AssetIn,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.update))
    ],
) -> dict[str, Any]:
    cid = _company(current_user)
    a = await _load(session, cid, asset_id)
    tag = body.asset_tag.strip()
    clash = (
        await session.execute(
            select(Asset).where(
                Asset.company_id == cid,
                func.lower(Asset.asset_tag) == tag.lower(),
                Asset.id != a.id,
                Asset.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if clash:
        raise HTTPException(status_code=409, detail=f'There is already an asset tagged "{tag}".')

    a.asset_tag = tag
    a.name = body.name.strip()
    a.category = body.category
    a.brand = (body.brand or "").strip() or None
    a.model = (body.model or "").strip() or None
    a.serial_number = (body.serial_number or "").strip() or None
    a.licence_key = (body.licence_key or "").strip() or None
    a.ownership = body.ownership
    a.purchased_on = _naive(body.purchased_on)
    a.purchase_cost = body.purchase_cost
    a.notes = (body.notes or "").strip() or None
    await session.commit()
    await session.refresh(a)
    return _asset_out(a)


class StatusIn(BaseModel):
    status: Literal["available", "in_repair", "retired", "lost"]
    note: str | None = None


@router.post("/{asset_id}/status")
async def set_status(
    asset_id: uuid.UUID,
    body: StatusIn,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.update))
    ],
) -> dict[str, Any]:
    """Move an asset out of or back into circulation.

    "assigned" is not settable here: it is a consequence of an assignment existing, not a label
    somebody applies. Letting it be set by hand is how the status and the assignment table start
    disagreeing about whether a laptop is out.
    """
    cid = _company(current_user)
    a = await _load(session, cid, asset_id)
    if _open_assignment(a) is not None:
        raise HTTPException(
            status_code=409, detail="Somebody is holding this. Take it back first, then change its status."
        )
    a.status = body.status
    if body.note:
        # Appended rather than replacing: the note explains why the status changed, and the
        # previous reason is usually the context for this one.
        a.notes = ((a.notes + "\n") if a.notes else "") + body.note.strip()
    await session.commit()
    await session.refresh(a)
    return _asset_out(a)


@router.delete("/{asset_id}", status_code=204)
async def delete_asset(
    asset_id: uuid.UUID,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.delete))
    ],
) -> None:
    """Soft delete, and refused while somebody has it.

    Deleting an asset that is out would drop it off every list including the exit checklist,
    which is precisely the record you need to get it back.
    """
    cid = _company(current_user)
    a = await _load(session, cid, asset_id)
    if _open_assignment(a) is not None:
        raise HTTPException(
            status_code=409, detail="Somebody is holding this. Take it back before removing it."
        )
    a.deleted_at = _now()
    await session.commit()


# ───────────────────────────── assignment ────────────────────────────────────


class AssignIn(BaseModel):
    employee_id: uuid.UUID
    due_back_on: datetime | None = None
    note: str | None = None


@router.post("/{asset_id}/assign", status_code=201)
async def assign(
    asset_id: uuid.UUID,
    body: AssignIn,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.update))
    ],
) -> dict[str, Any]:
    """Hand an asset to somebody."""
    cid = _company(current_user)
    a = await _load(session, cid, asset_id)

    if _open_assignment(a) is not None:
        raise HTTPException(status_code=409, detail="Somebody already has this one.")
    if a.status in ("retired", "lost"):
        raise HTTPException(status_code=409, detail=f"This asset is marked {a.status}.")

    emp = (
        await session.execute(
            select(Employee).where(
                Employee.id == body.employee_id,
                # Scoped on the way in: an employee id from another company must never become
                # the holder of this company's kit.
                Employee.company_id == cid,
                Employee.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    row = AssetAssignment(
        asset_id=a.id,
        employee_id=emp.id,
        assigned_at=_now(),
        assigned_by=getattr(current_user, "id", None),
        due_back_on=_naive(body.due_back_on),
        note=(body.note or "").strip() or None,
        company_id=cid,
    )
    session.add(row)
    # Kept in step here, in one place, so the register and the assignments cannot disagree.
    a.status = "assigned"
    await session.commit()
    await session.refresh(a)
    return _asset_out(a)


class ReturnIn(BaseModel):
    condition: Literal["good", "damaged", "unusable", "not_returned"] = "good"
    note: str | None = None


@router.post("/{asset_id}/return")
async def take_back(
    asset_id: uuid.UUID,
    body: ReturnIn,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.update))
    ],
) -> dict[str, Any]:
    """Close the open assignment and put the asset back where its condition says it belongs.

    A damaged item does not go back on the shelf as available, and one recorded as never
    returned goes to `lost` rather than quietly becoming available again — which is what would
    happen if this only cleared the assignment.
    """
    cid = _company(current_user)
    a = await _load(session, cid, asset_id)
    open_a = _open_assignment(a)
    if open_a is None:
        raise HTTPException(status_code=409, detail="Nobody is holding this one.")

    open_a.returned_at = _now()
    open_a.returned_to = getattr(current_user, "id", None)
    open_a.return_condition = body.condition
    if body.note:
        open_a.note = ((open_a.note + "\n") if open_a.note else "") + body.note.strip()

    a.status = {"good": "available", "damaged": "in_repair", "unusable": "retired", "not_returned": "lost"}[
        body.condition
    ]

    await session.commit()
    await session.refresh(a)
    return _asset_out(a)


@router.get("/{asset_id}/history")
async def history(
    asset_id: uuid.UUID,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.read))],
) -> dict[str, Any]:
    """Everyone who has ever held this asset, newest first."""
    cid = _company(current_user)
    a = await _load(session, cid, asset_id)
    return {
        "asset": _asset_out(a),
        "history": [
            {
                "id": str(x.id),
                "employee_id": str(x.employee_id),
                "employee_name": _employee_name(x.employee),
                "assigned_at": x.assigned_at.isoformat() if x.assigned_at else None,
                "due_back_on": x.due_back_on.isoformat() if x.due_back_on else None,
                "returned_at": x.returned_at.isoformat() if x.returned_at else None,
                "return_condition": x.return_condition,
                "note": x.note,
            }
            for x in a.assignments
        ],
    }


@router.get("/held-by/{employee_id}")
async def held_by(
    employee_id: uuid.UUID,
    session: DBSessionDep,
    current_user: Annotated[object, Depends(PermissionChecker(ModuleScope.employees, PermissionAction.read))],
) -> dict[str, Any]:
    """What one person is holding right now.

    This is what the exit checklist is built from, and what anyone asks before a last day.
    """
    cid = _company(current_user)
    rows = (
        (
            await session.execute(
                select(AssetAssignment)
                .where(
                    AssetAssignment.company_id == cid,
                    AssetAssignment.employee_id == employee_id,
                    AssetAssignment.returned_at.is_(None),
                )
                .order_by(AssetAssignment.assigned_at.desc())
            )
        )
        .scalars()
        .unique()
        .all()
    )
    now = _now()
    return {
        "employee_id": str(employee_id),
        "count": len(rows),
        "assets": [
            {
                "assignment_id": str(x.id),
                "asset_id": str(x.asset_id),
                "asset_tag": x.asset.asset_tag,
                "name": x.asset.name,
                "category": x.asset.category,
                "assigned_at": x.assigned_at.isoformat() if x.assigned_at else None,
                "due_back_on": x.due_back_on.isoformat() if x.due_back_on else None,
                "overdue": bool(x.due_back_on and x.due_back_on < now),
            }
            for x in rows
        ],
    }
