"""Expenses — claims, approval and reimbursement.

Croar runs payroll but could not reimburse a rupee. Oorwin's Expenses module has tabs for
Expenses, Pending Approvals, Receipt Inbox, Expense Payments, Advance Expenses and Advance
Expense Payments — read from their live product — and the split it implies is the right one:
a claim is not the same thing as a payment, and an advance is neither.

Three ideas, three tables:

  * a **claim** is a request: several line items, submitted together, approved or rejected as a
    whole. Items live separately because a claim is a batch — one taxi, two meals, one hotel —
    and approving "the taxi but not the hotel" has to be expressible.
  * an **advance** is money paid out before the spend. It is not a claim with a negative sign:
    it is settled against later claims, and the balance outstanding is a number somebody chases.
  * a **payment** is the transfer. Kept separate from approval because approving on Tuesday and
    paying on the 30th is the normal case, and collapsing them makes "approved but unpaid" —
    the state finance actually reports on — impossible to express.

Amounts are Numeric, never float. Money in a float is how you end up 0.01 short on a hundred
reimbursements and cannot explain why.
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import TIMESTAMP, Boolean, Date, ForeignKey, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text

from .base import EnterpriseBase

# draft → theirs to edit. submitted → waiting on somebody. approved → owed. paid → settled.
# rejected → refused with a reason. A claim in "approved" that is never paid is the state this
# whole module exists to make visible.
CLAIM_STATUSES = ("draft", "submitted", "approved", "rejected", "paid", "cancelled")

ADVANCE_STATUSES = ("requested", "approved", "rejected", "paid", "settled", "cancelled")

PAYMENT_METHODS = ("bank_transfer", "cash", "cheque", "payroll", "card")


class ExpenseCategory(EnterpriseBase):
    """What a line item is for, and the rules attached to it."""

    __tablename__ = "expense_categories"
    __table_args__ = (UniqueConstraint("company_id", "name", name="uq_expense_categories_company_name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The cap per item. Null means uncapped, which is not the same as zero — the API checks for
    # None rather than truthiness so a genuine cap of 0 would still work.
    limit_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    # Whether a claim in this category is refused without a receipt attached. A rule worth
    # having per category: a taxi needs one, a per-diem does not.
    receipt_required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    deleted_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)


class ExpenseClaim(EnterpriseBase):
    """A batch of spending submitted for approval together."""

    __tablename__ = "expense_claims"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    # Human-readable and sequential per company, because people refer to claims out loud and
    # nobody says a uuid.
    reference: Mapped[str] = mapped_column(String(30), nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, server_default="INR")

    # Recomputed from the items on every write rather than trusted from the client. A total the
    # client sends is a total the client can get wrong.
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0"))
    approved_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="draft", index=True)
    submitted_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # An advance this claim is being settled against, if any. This is the link that stops an
    # advance being quietly forgotten once the person has actually spent the money.
    advance_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("expense_advances.id", ondelete="SET NULL"), nullable=True
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)

    items = relationship(
        "ExpenseItem",
        back_populates="claim",
        order_by="ExpenseItem.spent_on",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    payments = relationship("ExpensePayment", back_populates="claim", lazy="selectin")
    employee = relationship("Employee", lazy="selectin")


class ExpenseItem(EnterpriseBase):
    """One line: what, when, how much, and the receipt."""

    __tablename__ = "expense_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("expense_claims.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("expense_categories.id", ondelete="SET NULL"), nullable=True
    )
    spent_on: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    merchant: Mapped[str | None] = mapped_column(String(160), nullable=True)
    # A path, not the bytes. Croar stores files elsewhere; putting a receipt image in a row
    # makes every list query drag megabytes it does not need.
    receipt_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Set when an approver cuts a single line rather than refusing the whole claim.
    approved_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())

    claim = relationship("ExpenseClaim", back_populates="items")
    category = relationship("ExpenseCategory", lazy="selectin")


class ExpenseAdvance(EnterpriseBase):
    """Money paid out before the spend, settled against later claims."""

    __tablename__ = "expense_advances"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    reference: Mapped[str] = mapped_column(String(30), nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[str] = mapped_column(String(300), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, server_default="INR")
    needed_by: Mapped[date | None] = mapped_column(Date, nullable=True)

    # What has been accounted for by claims so far. The outstanding balance is amount minus
    # this, and it is the number that gets chased at exit.
    settled_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0"))

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="requested", index=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)

    employee = relationship("Employee", lazy="selectin")


class ExpensePayment(EnterpriseBase):
    """The transfer. Separate from approval, because approving and paying are different days."""

    __tablename__ = "expense_payments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    claim_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("expense_claims.id", ondelete="CASCADE"), nullable=True, index=True
    )
    advance_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("expense_advances.id", ondelete="CASCADE"), nullable=True, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    paid_on: Mapped[date] = mapped_column(Date, nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=False, server_default="bank_transfer")
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    paid_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())

    claim = relationship("ExpenseClaim", back_populates="payments")
