"""Credits ledger — tracks consumption of platform credits per company.

Two tables:
- ``credit_accounts``      one row per company holding the running balance + lifetime totals.
- ``credit_transactions``  append-only ledger; every grant/usage/adjustment is one signed row.

Balance is authoritative on the account row (fast reads) and equals the sum of transaction
amounts (audited). ``amount`` is signed: negative = usage (debit), positive = grant/top-up.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from .base import EnterpriseBase


class CreditAccount(EnterpriseBase):
    """Per-company credit wallet. One row per company (unique)."""

    __tablename__ = "credit_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    balance: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    total_granted: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    total_used: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CreditTransaction(EnterpriseBase):
    """One ledger entry. ``amount`` signed: usage < 0, grants > 0."""

    __tablename__ = "credit_transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    # "usage" | "grant" | "adjustment"
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="usage")
    # Which meter: "sourcing" | "ai" | "assessment" | "interview" (usage rows only).
    category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Specific action e.g. "profile", "jd_generation", "attempt", "session".
    action: Mapped[str | None] = mapped_column(String(60), nullable=True)
    units: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=1)
    unit_cost: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)  # signed
    balance_after: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reference_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_credit_tx_company_created", "company_id", "created_at"),
        Index("ix_credit_tx_company_category", "company_id", "category"),
    )
