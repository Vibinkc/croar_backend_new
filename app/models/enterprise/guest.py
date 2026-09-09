"""Guests — outside people who can see some of your jobs, and nothing else.

A hiring manager at a client, a department head, an interviewer who does not work here: people
who need to look at candidates on one or two jobs and must not be able to look at anything else.
Manatal models them as a separate record type rather than as a user with a thin role, and that
is the right call — a guest has no password to reset, no seat, no permissions matrix, and
turning one off must be one action that cannot half-succeed.

Scope is two fields, matching Manatal exactly: a department, and an access level that is either
"every job in that department" or "only these named jobs". The two are stored side by side and
the API refuses a combination that contradicts itself, because the failure mode of a scope you
can set wrongly is silent over-sharing.

    A note on "department": Croar stores a department as free text on a job, and separately has
    a departments table used by the HR side for employees. The two are unrelated. Rather than
    force a decision about which becomes canonical — a decision that touches employees, payroll
    and reporting, and belongs to whoever owns those — a guest's department is the job field,
    matched by name. That is the one that actually groups jobs today, and it makes guest scope
    work now without pre-empting that call.

Access is by link, not by password. A guest gets a token; the token is the credential; revoking
the guest kills the token. There is no account to compromise and nothing to reset.
"""

import uuid

from sqlalchemy import TIMESTAMP, Column, ForeignKey, String, Table, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text

from .base import EnterpriseBase

# What a guest may see. Manatal offers exactly these two and no more, which is a good sign:
# every extra option here is another way to share something by accident.
ACCESS_ALL_DEPARTMENT_JOBS = "all_department_jobs"
ACCESS_SPECIFIC_JOBS = "specific_jobs"
GUEST_ACCESS_LEVELS = (ACCESS_ALL_DEPARTMENT_JOBS, ACCESS_SPECIFIC_JOBS)

# invited → the link has been created but never opened. active → they have used it at least
# once. revoked → the link is dead. Kept as a status rather than a pair of booleans so there is
# no way to be both revoked and active.
GUEST_STATUSES = ("invited", "active", "revoked")

guest_jobs = Table(
    "guest_jobs",
    EnterpriseBase.metadata,
    Column("guest_id", UUID(as_uuid=True), ForeignKey("guests.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "job_id", UUID(as_uuid=True), ForeignKey("job_requirements.id", ondelete="CASCADE"), primary_key=True
    ),
    Column("added_at", TIMESTAMP, server_default=func.now()),
)


class Guest(EnterpriseBase):
    __tablename__ = "guests"
    __table_args__ = (
        # Per company, not globally: the same person can be a guest of two agencies, and a
        # global constraint would let the first one block the second.
        UniqueConstraint("company_id", "email", name="uq_guests_company_email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )

    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Their name as it appears to your team on shared notes. Manatal asks for both, and the
    # distinction earns its place: "Dr. Priya Raman" signs off, "Priya (Acme)" is the label your
    # recruiters need to recognise in a comment thread.
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    department: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    access_level: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=ACCESS_ALL_DEPARTMENT_JOBS
    )

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="invited", index=True)
    # The credential itself. Unique across the table so a lookup by token needs no company hint,
    # and indexed because every guest request starts with it.
    invite_token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    invited_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)
    last_seen_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)

    jobs = relationship("JobRequirement", secondary=guest_jobs, lazy="selectin")
