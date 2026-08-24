"""Credits calculation & ledger service.

Central place that (a) defines how many credits each action costs, (b) writes ledger entries
and keeps each company's ``credit_accounts`` balance in sync, and (c) exposes a billing
context so the shared LLM helpers can auto-meter AI usage without every endpoint wiring it up.

Design goals:
- **One source of truth for costs** — ``CREDIT_COSTS`` below.
- **Never break a feature over billing** — recording is best-effort; any error is swallowed
  and logged, so a metering failure can't fail a user's sourcing/AI/assessment request.
- **Tracking first** — usage is always recorded even if it drives the balance negative
  (this is a usage tracker, not a hard paywall). ``has_credits`` lets callers gate if they want.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from loguru import logger
from sqlalchemy import func, select

from app.core.database import db_manager
from app.models.enterprise.credits import CreditAccount, CreditTransaction

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

# --------------------------------------------------------------------------------------
# Cost model — credits charged per unit of each (category, action). Tune freely; this is
# the ONLY place rates live. Unknown actions fall back to the category's "default".
# --------------------------------------------------------------------------------------
CREDIT_COSTS: dict[str, dict[str, float]] = {
    # Profile sourcing — charged per candidate profile returned.
    "sourcing": {"profile": 1.0, "default": 1.0},
    # General AI usage — billed on ACTUAL Claude token burn (input + output tokens from each
    # response), auto-metered at the shared Claude layer so every AI feature is covered. Output
    # tokens cost more than input (mirrors model pricing ~5x). Rates are credits per 1,000 tokens.
    "ai": {
        "input_per_1k": 0.20,  # 0.20 credit per 1K input (prompt) tokens
        "output_per_1k": 1.00,  # 1.00 credit per 1K output (generated) tokens
    },
    # Assessments — charged per completed candidate attempt.
    "assessment": {"attempt": 3.0, "default": 3.0},
    # Interviews — charged per AI interview session.
    "interview": {"session": 5.0, "default": 5.0},
}

# Free credits seeded to a company the first time its wallet is touched.
INITIAL_FREE_CREDITS = Decimal("1000.00")

CATEGORIES = ("sourcing", "ai", "assessment", "interview")


def cost_for(category: str, action: str | None, units: float = 1.0) -> Decimal:
    """Credits for ``units`` of (category, action), from the cost table."""
    table = CREDIT_COSTS.get(category, {})
    rate = table.get(action or "default", table.get("default", 0.0))
    return (Decimal(str(rate)) * Decimal(str(units))).quantize(Decimal("0.01"))


def ai_credits_for_tokens(input_tokens: int, output_tokens: int) -> Decimal:
    """AI credits for one call, from its real token burn (input + weighted output)."""
    rates = CREDIT_COSTS["ai"]
    inp = Decimal(str(input_tokens or 0)) / Decimal("1000") * Decimal(str(rates["input_per_1k"]))
    out = Decimal(str(output_tokens or 0)) / Decimal("1000") * Decimal(str(rates["output_per_1k"]))
    # 4dp keeps small calls from rounding to zero; balance column still stores 2dp.
    return (inp + out).quantize(Decimal("0.0001"))


# --------------------------------------------------------------------------------------
# Billing context — set per authenticated request so LLM-layer auto-metering knows who to bill.
# --------------------------------------------------------------------------------------
@dataclass
class BillingContext:
    company_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None


_ctx: contextvars.ContextVar[BillingContext | None] = contextvars.ContextVar("billing_ctx", default=None)


def set_billing_context(company_id: uuid.UUID | None, user_id: uuid.UUID | None = None) -> None:
    _ctx.set(BillingContext(company_id=company_id, user_id=user_id))


def get_billing_context() -> BillingContext | None:
    return _ctx.get()


# --------------------------------------------------------------------------------------
# Core ledger ops
# --------------------------------------------------------------------------------------
async def _get_or_create_account(s: AsyncSession, company_id: uuid.UUID) -> CreditAccount:
    acct = (
        await s.execute(select(CreditAccount).where(CreditAccount.company_id == company_id))
    ).scalar_one_or_none()
    if acct is None:
        acct = CreditAccount(
            company_id=company_id,
            balance=INITIAL_FREE_CREDITS,
            total_granted=INITIAL_FREE_CREDITS,
            total_used=Decimal("0.00"),
        )
        s.add(acct)
        await s.flush()
    return acct


async def _write(
    s: AsyncSession,
    company_id: uuid.UUID,
    *,
    kind: str,
    amount: Decimal,
    category: str | None = None,
    action: str | None = None,
    units: float = 1.0,
    unit_cost: Decimal | None = None,
    description: str | None = None,
    reference_type: str | None = None,
    reference_id: str | None = None,
    user_id: uuid.UUID | None = None,
    meta: dict[str, Any] | None = None,
) -> CreditTransaction:
    acct = await _get_or_create_account(s, company_id)
    acct.balance = Decimal(str(acct.balance)) + amount
    if amount < 0:
        acct.total_used = Decimal(str(acct.total_used)) + (-amount)
    else:
        acct.total_granted = Decimal(str(acct.total_granted)) + amount
    tx = CreditTransaction(
        company_id=company_id,
        kind=kind,
        category=category,
        action=action,
        units=Decimal(str(units)),
        unit_cost=(unit_cost if unit_cost is not None else Decimal("0")),
        amount=amount,
        balance_after=Decimal(str(acct.balance)),
        description=description,
        reference_type=reference_type,
        reference_id=(str(reference_id) if reference_id is not None else None),
        user_id=user_id,
        meta=meta,
    )
    s.add(tx)
    await s.flush()
    return tx


async def record_usage(
    company_id: uuid.UUID | None,
    category: str,
    action: str,
    *,
    units: float = 1.0,
    reference_type: str | None = None,
    reference_id: str | None = None,
    user_id: uuid.UUID | None = None,
    description: str | None = None,
    meta: dict[str, Any] | None = None,
    session: AsyncSession | None = None,
) -> None:
    """Record credit consumption. Best-effort: never raises to the caller.

    If ``session`` is given the write joins that transaction; otherwise a short-lived
    session is opened and committed on its own (used by the LLM auto-meter).
    """
    if company_id is None:
        return
    if units <= 0:
        return
    unit_cost = cost_for(category, action, 1.0)
    amount = -cost_for(category, action, units)
    try:
        if session is not None:
            await _write(
                session,
                company_id,
                kind="usage",
                amount=amount,
                category=category,
                action=action,
                units=units,
                unit_cost=unit_cost,
                description=description,
                reference_type=reference_type,
                reference_id=reference_id,
                user_id=user_id,
                meta=meta,
            )
        else:
            async with db_manager.session() as s:
                await _write(
                    s,
                    company_id,
                    kind="usage",
                    amount=amount,
                    category=category,
                    action=action,
                    units=units,
                    unit_cost=unit_cost,
                    description=description,
                    reference_type=reference_type,
                    reference_id=reference_id,
                    user_id=user_id,
                    meta=meta,
                )
                await s.commit()
    except Exception as exc:  # billing must never break a feature
        logger.warning(f"credit record_usage failed ({category}/{action}): {exc!r}")


async def record_ai_usage(
    company_id: uuid.UUID | None,
    input_tokens: int,
    output_tokens: int,
    *,
    action: str = "llm_call",
    user_id: uuid.UUID | None = None,
    description: str | None = None,
    meta: dict[str, Any] | None = None,
    session: AsyncSession | None = None,
) -> None:
    """Record ONE AI call charged on its real token burn. Best-effort: never raises."""
    if company_id is None:
        return
    credits = ai_credits_for_tokens(input_tokens, output_tokens)
    if credits <= 0:
        return
    total = (input_tokens or 0) + (output_tokens or 0)
    m = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_per_1k": CREDIT_COSTS["ai"]["input_per_1k"],
        "output_per_1k": CREDIT_COSTS["ai"]["output_per_1k"],
        **(meta or {}),
    }
    try:
        if session is not None:
            await _write(
                session,
                company_id,
                kind="usage",
                amount=-credits,
                category="ai",
                action=action,
                units=total,
                unit_cost=Decimal("0"),
                description=description,
                reference_type="ai_call",
                user_id=user_id,
                meta=m,
            )
        else:
            async with db_manager.session() as s:
                await _write(
                    s,
                    company_id,
                    kind="usage",
                    amount=-credits,
                    category="ai",
                    action=action,
                    units=total,
                    unit_cost=Decimal("0"),
                    description=description,
                    reference_type="ai_call",
                    user_id=user_id,
                    meta=m,
                )
                await s.commit()
    except Exception as exc:
        logger.warning(f"credit record_ai_usage failed ({action}): {exc!r}")


async def grant(
    company_id: uuid.UUID,
    amount: float,
    *,
    description: str | None = None,
    user_id: uuid.UUID | None = None,
    session: AsyncSession | None = None,
) -> Decimal:
    """Add credits (top-up / plan grant). Returns the new balance."""
    amt = Decimal(str(amount)).quantize(Decimal("0.01"))
    if session is not None:
        tx = await _write(
            session, company_id, kind="grant", amount=amt, description=description, user_id=user_id
        )
        return Decimal(str(tx.balance_after))
    async with db_manager.session() as s:
        tx = await _write(s, company_id, kind="grant", amount=amt, description=description, user_id=user_id)
        await s.commit()
        return Decimal(str(tx.balance_after))


async def get_balance(company_id: uuid.UUID, session: AsyncSession) -> dict[str, Any]:
    acct = await _get_or_create_account(session, company_id)
    return {
        "balance": float(acct.balance),
        "total_granted": float(acct.total_granted),
        "total_used": float(acct.total_used),
    }


async def has_credits(company_id: uuid.UUID, session: AsyncSession, needed: float = 1.0) -> bool:
    acct = await _get_or_create_account(session, company_id)
    return Decimal(str(acct.balance)) >= Decimal(str(needed))


async def get_summary(company_id: uuid.UUID, session: AsyncSession) -> dict[str, Any]:
    """Usage totals broken down by meter category (lifetime)."""
    rows = (
        await session.execute(
            select(
                CreditTransaction.category,
                func.coalesce(func.sum(-CreditTransaction.amount), 0),
                func.count(),
            )
            .where(CreditTransaction.company_id == company_id, CreditTransaction.kind == "usage")
            .group_by(CreditTransaction.category)
        )
    ).all()
    by_category = {cat or "other": {"credits": float(total), "count": int(cnt)} for cat, total, cnt in rows}
    for c in CATEGORIES:
        by_category.setdefault(c, {"credits": 0.0, "count": 0})
    bal = await get_balance(company_id, session)
    return {**bal, "by_category": by_category}
