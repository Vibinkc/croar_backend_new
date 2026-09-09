"""Objectives and review cycles — targets, and the process that judges them.

Croar could already measure people: skill assessments, video reviews, 360s, surveys. It could
not set anyone a target. Assessments answer "how did they do"; objectives answer "against what",
and without the second the first has nothing to be scored relative to.

Oorwin's Performance module was read from their live product. It is explicitly three steps —
"Setup Review Process → Create a List of objectives → Assign objectives" — with tabs for My,
Team and All objectives, an Objective Library, and Manage Reviews. That shape is kept.

Four tables:

  * `objective_templates` — the library. Objectives that recur every cycle, written once.
  * `review_cycles` — the period being reviewed, and the windows within it. A cycle is a thing
    with dates, not a label: self-review closes before manager review opens, and that ordering
    is the whole reason a cycle exists rather than a free-for-all.
  * `objectives` — one target for one person in one cycle, with a measurable target and a
    current value. Progress is computed, never stored: a stored percentage goes stale the moment
    the current value moves and then two numbers disagree on screen.
  * `reviews` — one person's review within one cycle, with the self and manager halves as
    separate columns because they are written by different people at different times.
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text

from .base import EnterpriseBase

# draft → being written. active → the cycle is running. closed → done, and nothing in it can be
# edited, which is what makes a past review trustworthy.
CYCLE_STATUSES = ("draft", "active", "closed")

OBJECTIVE_STATUSES = ("not_started", "in_progress", "at_risk", "achieved", "missed", "dropped")

# How an objective is measured. A percentage, an absolute number, a currency amount, or simply
# done/not done — which covers the objectives that are genuinely binary and would be distorted
# by forcing a number onto them.
OBJECTIVE_MEASURES = ("percent", "number", "currency", "boolean")

# Where an objective sits. Company objectives cascade to teams and individuals; keeping the
# level explicit is what makes "show me everything rolling up to this" answerable.
OBJECTIVE_LEVELS = ("company", "team", "individual")

# not_started → nothing written. self_done → the employee has submitted theirs. manager_done →
# the manager has too. shared → the employee has seen the manager's. The last step is separate
# because a review the person has not read is not a review that has happened.
REVIEW_STATUSES = ("not_started", "self_done", "manager_done", "shared", "acknowledged")


class ReviewCycle(EnterpriseBase):
    """The period being reviewed, and the windows within it."""

    __tablename__ = "review_cycles"
    __table_args__ = (UniqueConstraint("company_id", "name", name="uq_review_cycles_company_name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)

    # The two windows. Self closes before manager opens, and the API enforces that ordering —
    # a manager writing before the self-review is submitted defeats the point of asking.
    self_review_opens: Mapped[date | None] = mapped_column(Date, nullable=True)
    self_review_closes: Mapped[date | None] = mapped_column(Date, nullable=True)
    manager_review_opens: Mapped[date | None] = mapped_column(Date, nullable=True)
    manager_review_closes: Mapped[date | None] = mapped_column(Date, nullable=True)

    # The scale reviews are scored on. Stored per cycle because changing it mid-flight would
    # silently rescale every review already written.
    rating_max: Mapped[int] = mapped_column(Integer, nullable=False, server_default="5")

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="draft", index=True)
    closed_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)

    objectives = relationship("Objective", back_populates="cycle", lazy="selectin")
    reviews = relationship("Review", back_populates="cycle", lazy="selectin")


class ObjectiveTemplate(EnterpriseBase):
    """The library — an objective worth writing once and assigning every cycle."""

    __tablename__ = "objective_templates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    measure: Mapped[str] = mapped_column(String(20), nullable=False, server_default="percent")
    target_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(30), nullable=True)
    level: Mapped[str] = mapped_column(String(20), nullable=False, server_default="individual")
    # Free text rather than a foreign key: "Engineering" here is a label for grouping the
    # library, not a link to a department record, and the two department concepts in Croar are
    # unresolved. Better a label that is honestly a label.
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    deleted_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)


class Objective(EnterpriseBase):
    """One target, for one person or team, in one cycle."""

    __tablename__ = "objectives"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    cycle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("review_cycles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Null for a company objective, which belongs to everyone and therefore to nobody in
    # particular.
    employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # What this rolls up into. Self-referential so a team objective can hang off a company one
    # and an individual's off the team's, which is the whole idea of cascading goals.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("objectives.id", ondelete="SET NULL"), nullable=True, index=True
    )
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("objective_templates.id", ondelete="SET NULL"), nullable=True
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    level: Mapped[str] = mapped_column(String(20), nullable=False, server_default="individual", index=True)
    measure: Mapped[str] = mapped_column(String(20), nullable=False, server_default="percent")
    # Progress is target vs current, computed on read. Storing a percentage means two numbers
    # that disagree the moment one of them moves.
    target_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    current_value: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, server_default=text("0"))
    unit: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # How much of the review this objective is worth. Weights within a person's set are checked
    # to total 100 when the cycle activates, not on every write — half-built sets never add up.
    weight: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="not_started", index=True)
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)

    cycle = relationship("ReviewCycle", back_populates="objectives")
    employee = relationship("Employee", lazy="selectin")
    checkins = relationship(
        "ObjectiveCheckin",
        back_populates="objective",
        order_by="ObjectiveCheckin.created_at.desc()",
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class ObjectiveCheckin(EnterpriseBase):
    """A progress update. The trail of how a number got where it got.

    Without these an objective is a number that changed and nobody can say when or why, which is
    exactly the argument that happens at review time.
    """

    __tablename__ = "objective_checkins"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    objective_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("objectives.id", ondelete="CASCADE"), nullable=False, index=True
    )
    value: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())

    objective = relationship("Objective", back_populates="checkins")


class Review(EnterpriseBase):
    """One person's review in one cycle."""

    __tablename__ = "reviews"
    __table_args__ = (
        # One review per person per cycle. Two would make "what did they score" ambiguous with
        # no correct answer.
        UniqueConstraint("cycle_id", "employee_id", name="uq_reviews_cycle_employee"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    cycle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("review_cycles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="not_started", index=True)

    # The two halves, written by different people at different times, which is why they are
    # separate columns rather than one "comments" field with a name attached.
    self_rating: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    self_comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    self_submitted_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)

    manager_rating: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    manager_comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    manager_submitted_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)

    shared_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)
    acknowledged_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)
    employee_response: Mapped[str | None] = mapped_column(Text, nullable=True)

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )

    cycle = relationship("ReviewCycle", back_populates="reviews")
    employee = relationship("Employee", lazy="selectin")
