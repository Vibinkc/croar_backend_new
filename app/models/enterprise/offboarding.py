"""Offboarding — the counterpart Croar never had to its onboarding module.

Modelled on Oorwin's flow, which was walked in their live product: a two-step
"Create Offboarding Initiation Request" — step one records the decision, step two is a
checklist. Their step one asks exactly this, and each field earns its place:

  * resignation or termination, because the two have different notice, different paperwork and
    different final-settlement rules;
  * a resignation date separate from the last working day, because notice is served between the
    two and the gap is the notice period;
  * whether the organisation would rehire, which is the one judgement nobody writes down and
    everybody needs three years later when the person applies again;
  * a reason, with free text when the reason is "other".

The checklist is the part that makes this more than a status field. It is seeded when the
offboarding is created, and one task is generated per asset the person is actually holding — so
"return the laptop" is not a line somebody remembered to type, it is a row that exists because
the asset table says they have it. That link is the whole point: a checklist that cannot see the
assets is a to-do list, not an exit process.

Status runs requested → approved → in_progress → completed, with rejected and cancelled as the
two ways out. It is one column rather than a set of booleans so a record cannot be simultaneously
rejected and completed, which is exactly the state a pile of flags drifts into.
"""

import uuid

from sqlalchemy import TIMESTAMP, Boolean, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text

from .base import EnterpriseBase

OFFBOARDING_TYPES = ("resignation", "termination")

# requested → somebody asked. approved → a decision was taken and the checklist is live.
# completed → every required task is done. rejected/cancelled are the two endings that are not
# an exit: the first is a refusal, the second is the person staying.
OFFBOARDING_STATUSES = ("requested", "approved", "in_progress", "completed", "rejected", "cancelled")

# Manatal-style reason list is not the model here; Oorwin's is a short enum with an "other"
# escape hatch, which is right — a fixed list you can report on, plus room for the case nobody
# anticipated.
OFFBOARDING_REASONS = (
    "better_opportunity",
    "compensation",
    "relocation",
    "personal",
    "health",
    "performance",
    "misconduct",
    "redundancy",
    "end_of_contract",
    "retirement",
    "other",
)

# What the checklist groups into. The categories are the departments that have to sign off,
# which is why they are these five and not a free-text tag.
TASK_CATEGORIES = ("assets", "access", "finance", "hr", "knowledge")


class Offboarding(EnterpriseBase):
    __tablename__ = "offboardings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )

    offboarding_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # When they told you. Distinct from the last working day: the space between the two is the
    # notice period actually served, which is what a final settlement is calculated from.
    resignation_date: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)
    last_working_day: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True, index=True)

    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    reason_other: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Tri-state on purpose. True/False are judgements someone made; NULL is "nobody decided",
    # which is not the same as "no" and must not be reported as one.
    rehire_eligible: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="requested", index=True)

    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    requested_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, nullable=False, server_default=func.now())
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)

    employee = relationship("Employee", lazy="selectin")
    tasks = relationship(
        "OffboardingTask",
        back_populates="offboarding",
        order_by="OffboardingTask.position",
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class OffboardingTask(EnterpriseBase):
    """One line on the exit checklist.

    `asset_id` is what connects this to the asset register. A task carrying one is a specific
    thing to get back, and completing it is what returns that asset — so the checklist and the
    asset register cannot disagree about whether the laptop came home.
    """

    __tablename__ = "offboarding_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    offboarding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("offboardings.id", ondelete="CASCADE"), nullable=False, index=True
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(20), nullable=False, server_default="hr", index=True)
    position: Mapped[int] = mapped_column(nullable=False, server_default="0")

    # A task nobody owns is a task nobody does, but forcing an owner at seed time would mean
    # guessing — so it is nullable and the UI nags rather than the schema refusing.
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    due_on: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)

    # Set when this task is "return X". Completing such a task returns the asset for real.
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )
    # A task that must be done before the offboarding can complete. Seeded true for assets and
    # access, because those are the two that cost money and create risk when skipped.
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    done: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    done_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)
    done_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )

    offboarding = relationship("Offboarding", back_populates="tasks")
