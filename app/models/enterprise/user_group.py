"""User groups — a named set of people, and the roles they all get by being in it.

Manatal's own description of the feature is "organize users, simplifying permissions and access
management", and the second half is the part that matters. A group that only lists names is a
label; the reason to build one is that granting a role to eight people should be one action, and
revoking it should also be one action.

So a group carries roles, and a member's effective permissions are their own roles plus every
role of every group they are in. That is a union, never a subtraction: leaving a group can only
take away what the group gave, and can never remove a permission the person holds directly.
This matters because the alternative — letting a group deny things — makes "why can this person
not see X" unanswerable without simulating the whole membership graph.

Membership is its own table rather than a column on the user, so one person can sit in several
groups and removing them from one leaves no trace on their record.
"""

import uuid

from sqlalchemy import TIMESTAMP, Column, ForeignKey, String, Table, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship
from sqlalchemy.sql import text

from .base import EnterpriseBase

user_group_members = Table(
    "user_group_members",
    EnterpriseBase.metadata,
    Column(
        "group_id", UUID(as_uuid=True), ForeignKey("user_groups.id", ondelete="CASCADE"), primary_key=True
    ),
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("added_at", TIMESTAMP, server_default=func.now()),
)

user_group_roles = Table(
    "user_group_roles",
    EnterpriseBase.metadata,
    Column(
        "group_id", UUID(as_uuid=True), ForeignKey("user_groups.id", ondelete="CASCADE"), primary_key=True
    ),
    Column("role_id", UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("assigned_at", TIMESTAMP, server_default=func.now()),
)


class UserGroup(EnterpriseBase):
    __tablename__ = "user_groups"
    __table_args__ = (
        # Two groups called "Recruiters" in one company is a filing mistake, not a feature, and
        # it makes every "which group granted this?" question ambiguous.
        UniqueConstraint("company_id", "name", name="uq_user_groups_company_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Manatal uploads a photo per group. Croar shows an initial chip everywhere else it shows a
    # person or a team, so this is the chip's colour rather than a file: one stored field, no
    # upload pipeline, and it still reads as "that group" at a glance in a list.
    colour: Mapped[str | None] = mapped_column(String(7), nullable=True)

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)

    members = relationship(
        "EnterpriseUser",
        secondary=user_group_members,
        # The reverse side is eager too. Every permission check reads user.groups, and a lazy
        # load there happens inside a sync callable on an async session, which raises rather
        # than fetching — the failure would look like a 500 on an unrelated endpoint.
        backref=backref("groups", lazy="selectin"),
        lazy="selectin",
    )
    roles = relationship("Role", secondary=user_group_roles, lazy="selectin")
