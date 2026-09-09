"""Activities — scheduled calls, meetings and interviews a recruiter plans.

Manatal's Activities screen is a calendar of appointments, not an audit trail: Title, Type,
Related To, Date, Time, Duration, Assignees, with day/week/month/list views. Croar had nothing
equivalent. It has `job_activities`, but that is an audit LOG — what already happened, written
by the system — and `interview_schedules`, which is narrower: one interviewer, tied to an
application, no title, type or duration. Neither can carry "ring this candidate back on Tuesday
for 30 minutes".

`related_to` is deliberately two nullable foreign keys rather than a generic (type, id) pair.
Only two things are ever worth attaching an activity to here, and real columns mean the
database enforces that the target exists and cleans up after itself when it does not.
"""

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Column, ForeignKey, Integer, String, Table, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text

from .base import EnterpriseBase

# Manatal's set. `other` is the fallback their own demo row uses ("Related To: Other"), so it
# is a real member of the vocabulary rather than a catch-all we invented.
ACTIVITY_TYPES = ("call", "meeting", "interview", "email", "task", "other")

# Who the activity is for. An activity can have several assignees, so this is its own table
# rather than a column — a shared interview panel is the normal case, not the exception.
activity_assignees = Table(
    "activity_assignees",
    EnterpriseBase.metadata,
    Column(
        "activity_id", UUID(as_uuid=True), ForeignKey("activities.id", ondelete="CASCADE"), primary_key=True
    ),
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
)


class Activity(EnterpriseBase):
    __tablename__ = "activities"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    activity_type: Mapped[str] = mapped_column(String(30), nullable=False, default="other")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Date and time as one instant. Manatal shows them in two columns, but storing them apart
    # would make "the next hour" a two-column comparison and invite the two to disagree.
    starts_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False, index=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)

    # Related To. Both null means "Other", which is exactly how their demo row reads.
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=True, index=True
    )
    job_requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_requirements.id", ondelete="CASCADE"), nullable=True, index=True
    )

    # Completion is a timestamp, not a boolean: "done" and "done at 4pm on Tuesday" cost the
    # same to store, and only one of them can be reported on later.
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)

    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )

    assignees = relationship("EnterpriseUser", secondary=activity_assignees, lazy="selectin")
    candidate = relationship("Candidate", lazy="joined")
    job = relationship("JobRequirement", lazy="joined")
