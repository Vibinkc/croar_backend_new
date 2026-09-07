"""Candidate folders — a recruiter's own way of grouping people, independent of any job.

Manatal has these alongside jobs, and the distinction is worth keeping. A job pipeline is a
process: a candidate on it has a stage, gets emailed, moves or is dropped. A folder is a
bookmark: "strong React people", "revisit in six months", "referred by the CTO". Putting
someone in one changes nothing about them and triggers nothing, which is exactly why it is a
safe thing to do while still deciding.

Membership is its own table rather than an array column on the candidate, so a person can sit
in several folders and so removing them from one leaves no trace on the record itself.
"""

import uuid

from sqlalchemy import TIMESTAMP, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text

from .base import EnterpriseBase


class CandidateFolder(EnterpriseBase):
    __tablename__ = "candidate_folders"
    __table_args__ = (
        # Two folders called "Frontend" in one company is a filing mistake, not a feature.
        UniqueConstraint("company_id", "name", name="uq_candidate_folders_company_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )

    members: Mapped[list["CandidateFolderMember"]] = relationship(
        back_populates="folder", cascade="all, delete-orphan", lazy="selectin"
    )


class CandidateFolderMember(EnterpriseBase):
    __tablename__ = "candidate_folder_members"
    __table_args__ = (
        # Adding the same person twice is a no-op the UI should be free to attempt.
        UniqueConstraint("folder_id", "candidate_id", name="uq_candidate_folder_members"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    folder_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidate_folders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Deleting a candidate removes their memberships: a folder pointing at a deleted person is
    # a broken row, not a record worth keeping.
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    added_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    added_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())

    folder: Mapped[CandidateFolder] = relationship(back_populates="members")
