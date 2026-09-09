"""Three things an employee can ask for: a signature, help, and a change to their own record.

They live in one file because they are the same shape — somebody raises a request, somebody
else acts on it, and the trail of who decided what is the point. They are three tables rather
than one polymorphic `requests` table because the middle of each differs completely: a signature
has a document and a deadline, a ticket has a queue and a priority, and a field change has a
before and an after that must be applied atomically on approval.

    A single generic table with a JSON payload would look tidier and be worse. "Which fields
    have people asked to change" becomes a JSON scan, "how many tickets are open in IT" needs a
    filter on an untyped column, and nothing can be constrained.

E-SIGN is modelled on Oorwin's Document Submissions, read from their live product: Submission
Id, Requested To, Workflow, Document Name, Module, Requested On, Requested By, Due Date,
Submitted Date — with Awaiting / Submitted / Approved / Rejected as the states, and a Missing
Documents view. The "module" is what the signature belongs to, which is how one signature
request can be part of onboarding and another part of an exit.

HELP DESK is Oorwin's: My Requests, Work List, All Requests.

CHANGE APPROVALS is their pending-approvals queue, which showed Code, Employee Name, Subject,
Field name, Field value, Requested On — an employee asking to change one field on their own
record, and an approver seeing exactly which field and what to.
"""

import uuid
from datetime import date
from typing import Any

from sqlalchemy import TIMESTAMP, Boolean, Date, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text

from .base import EnterpriseBase

# ── e-sign ──────────────────────────────────────────────────────────────────
# awaiting → sent, nothing back. submitted → they signed and returned it. approved/rejected →
# somebody on this side looked at what came back. Oorwin's four, and the split matters: signed
# is not the same as accepted.
SIGNATURE_STATUSES = ("awaiting", "submitted", "approved", "rejected", "cancelled", "expired")

# What the request belongs to, so a signature can be part of onboarding or of an exit and the
# owning module can ask "what is still outstanding for this person".
SIGNATURE_MODULES = ("onboarding", "offboarding", "employee", "candidate", "other")

# ── help desk ───────────────────────────────────────────────────────────────
TICKET_STATUSES = ("open", "in_progress", "waiting", "resolved", "closed", "cancelled")
TICKET_PRIORITIES = ("low", "normal", "high", "urgent")

# ── change approvals ────────────────────────────────────────────────────────
CHANGE_STATUSES = ("pending", "approved", "rejected", "cancelled")

# Only these may be requested. An allow-list rather than "any column", because a change-request
# system that can set employee_id or company_id is a privilege-escalation path wearing a form.
CHANGEABLE_FIELDS = (
    "first_name",
    "middle_name",
    "last_name",
    "mobile",
    "phone_number",
    "city",
    "state",
    "country",
    "pincode",
    "blood_group",
    "marital_status",
    "bank_account_no",
    "uan",
    "esic_number",
    "pan_card_number",
    "aadhar_card_number",
    "passport_number",
    "about_yourself",
)


class SignatureRequest(EnterpriseBase):
    """A document sent to somebody to sign, and whether it came back."""

    __tablename__ = "signature_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    reference: Mapped[str] = mapped_column(String(30), nullable=False)
    document_name: Mapped[str] = mapped_column(String(200), nullable=False)
    document_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    module: Mapped[str] = mapped_column(String(20), nullable=False, server_default="other", index=True)
    # What the signature is attached to — an offboarding, an onboarding, an employee. Not a
    # foreign key, because it points at one of several tables depending on `module`.
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)

    # Who has to sign. An employee where there is one; otherwise just an email, because a
    # candidate signing an offer letter is not an employee yet.
    employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=True, index=True
    )
    signer_name: Mapped[str] = mapped_column(String(200), nullable=False)
    signer_email: Mapped[str] = mapped_column(String(254), nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="awaiting", index=True)
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)

    # The credential. Same reasoning as the guest portal: a link is the whole access, so it is
    # unpredictable and unique, and revoking is deleting it.
    access_token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    requested_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, nullable=False, server_default=func.now())
    submitted_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)
    # What they typed as their signature, plus when and from where. Not a drawn image: a typed
    # name with a timestamp and an IP is what most e-signature law actually asks for, and it is
    # honest about being that rather than pretending to be a wet signature.
    signed_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    signed_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
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


class TicketCategory(EnterpriseBase):
    """A help-desk queue: IT, Payroll, Facilities."""

    __tablename__ = "ticket_categories"
    __table_args__ = (UniqueConstraint("company_id", "name", name="uq_ticket_categories_company_name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Everything raised here lands with this person unless somebody reassigns it. Without a
    # default owner a queue is a place tickets go to be ignored.
    default_owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Hours. Used to compute whether a ticket is overdue, which is the only number on the list
    # that implies somebody has to act now.
    target_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    deleted_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)


class Ticket(EnterpriseBase):
    """One request for help."""

    __tablename__ = "tickets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    reference: Mapped[str] = mapped_column(String(30), nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ticket_categories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    priority: Mapped[str] = mapped_column(String(10), nullable=False, server_default="normal", index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="open", index=True)

    raised_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    # The employee the ticket is about, which is not always the person who raised it — a manager
    # raising something on behalf of their report is the common case.
    employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="SET NULL"), nullable=True, index=True
    )
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    due_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)
    resolved_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)
    closed_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)

    comments = relationship(
        "TicketComment",
        back_populates="ticket",
        order_by="TicketComment.created_at",
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class TicketComment(EnterpriseBase):
    """A reply on a ticket. `internal` keeps a note between agents off the requester's view."""

    __tablename__ = "ticket_comments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    internal: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())

    ticket = relationship("Ticket", back_populates="comments")


class ChangeRequest(EnterpriseBase):
    """An employee asking to change one field on their own record.

    Both the old and the new value are stored. The old one is not redundant: by the time somebody
    approves this, the record may have moved on, and an approval that overwrites a change nobody
    saw is worse than a refusal. It also makes the audit trail readable without a second lookup.
    """

    __tablename__ = "change_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    reference: Mapped[str] = mapped_column(String(30), nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    field_name: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    # JSONB so a value keeps its type. A date stored as text and a number stored as text both
    # come back needing a parse that the reader has to guess at.
    old_value: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending", index=True)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    requested_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, nullable=False, server_default=func.now())
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

    employee = relationship("Employee", lazy="selectin")
