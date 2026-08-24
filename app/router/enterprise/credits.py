"""Credits API — balance, usage summary, transaction history, cost table, and admin grants."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.dependencies import DBSessionDep, PermissionChecker, get_current_user
from app.models.enterprise.credits import CreditTransaction
from app.models.shared.constants import ModuleScope, PermissionAction
from app.payroll.deps import get_current_company_id
from app.services.enterprise import credit_service as cs

router = APIRouter(prefix="/credits", tags=["Credits"])

CompanyId = Annotated[uuid.UUID, Depends(get_current_company_id)]


@router.get("/balance")
async def get_credit_balance(company_id: CompanyId, session: DBSessionDep) -> dict[str, Any]:
    """Current wallet balance + lifetime granted/used for the company."""
    return await cs.get_balance(company_id, session)


@router.get("/summary")
async def get_credit_summary(company_id: CompanyId, session: DBSessionDep) -> dict[str, Any]:
    """Balance plus usage broken down by meter (sourcing / ai / assessment / interview)."""
    return await cs.get_summary(company_id, session)


@router.get("/costs")
async def get_credit_costs(_user: Annotated[object, Depends(get_current_user)]) -> dict[str, Any]:
    """The active credit cost table + starting free balance (for a pricing/usage page)."""
    return {"costs": cs.CREDIT_COSTS, "initial_free_credits": float(cs.INITIAL_FREE_CREDITS)}


@router.get("/history")
async def get_credit_history(
    company_id: CompanyId,
    session: DBSessionDep,
    category: str | None = Query(None, description="Filter to one meter"),
    kind: str | None = Query(None, description="usage | grant | adjustment"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    """Recent ledger entries, newest first."""
    stmt = select(CreditTransaction).where(CreditTransaction.company_id == company_id)
    if category:
        stmt = stmt.where(CreditTransaction.category == category)
    if kind:
        stmt = stmt.where(CreditTransaction.kind == kind)
    stmt = stmt.order_by(CreditTransaction.created_at.desc()).limit(limit).offset(offset)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": str(t.id),
            "kind": t.kind,
            "category": t.category,
            "action": t.action,
            "units": float(t.units),
            "amount": float(t.amount),
            "balance_after": float(t.balance_after),
            "description": t.description,
            "reference_type": t.reference_type,
            "reference_id": t.reference_id,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in rows
    ]


class GrantRequest(BaseModel):
    amount: float = Field(..., gt=0, description="Credits to add")
    description: str | None = None


@router.post("/grant")
async def grant_credits(
    request: GrantRequest,
    session: DBSessionDep,
    _user: Annotated[object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.moderate))],
    company_id: CompanyId,
) -> dict[str, Any]:
    """Admin top-up: add credits to the company wallet."""
    new_balance = await cs.grant(
        company_id, request.amount, description=request.description or "Manual top-up", session=session
    )
    await session.commit()
    return {"balance": float(new_balance), "granted": request.amount}
