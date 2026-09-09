"""add user groups and guests

Two of the Administration gaps under Account and Users.

Groups carry roles as well as members, because a group that only lists names is a label — the
reason to build one is that granting a role to eight people should be one action.

Guests are a separate record type rather than thin users: no password, no seat, no permissions
matrix, and revoking one is a single action that cannot half-succeed.

Revision ID: e4b6f2d8a913
Revises: d2a8c5b71e64
Create Date: 2026-09-09

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "e4b6f2d8a913"
down_revision: Union[str, None] = "d2a8c5b71e64"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_groups",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("colour", sa.String(length=7), nullable=True),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.TIMESTAMP(), nullable=True),
        # Per company, not global: "Recruiters" is a name every company will want.
        sa.UniqueConstraint("company_id", "name", name="uq_user_groups_company_name"),
    )
    op.create_index(op.f("ix_user_groups_company_id"), "user_groups", ["company_id"])

    op.create_table(
        "user_group_members",
        sa.Column(
            "group_id",
            UUID(as_uuid=True),
            sa.ForeignKey("user_groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("added_at", sa.TIMESTAMP(), server_default=sa.func.now()),
    )

    op.create_table(
        "user_group_roles",
        sa.Column(
            "group_id",
            UUID(as_uuid=True),
            sa.ForeignKey("user_groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "role_id", UUID(as_uuid=True), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("assigned_at", sa.TIMESTAMP(), server_default=sa.func.now()),
    )

    op.create_table(
        "guests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("department", sa.String(length=255), nullable=True),
        sa.Column("access_level", sa.String(length=30), nullable=False, server_default="all_department_jobs"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="invited"),
        sa.Column("invite_token", sa.String(length=64), nullable=False),
        sa.Column("invited_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("last_seen_at", sa.TIMESTAMP(), nullable=True),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.TIMESTAMP(), nullable=True),
        # Per company: the same person can be a guest of two agencies.
        sa.UniqueConstraint("company_id", "email", name="uq_guests_company_email"),
    )
    op.create_index(op.f("ix_guests_company_id"), "guests", ["company_id"])
    op.create_index(op.f("ix_guests_department"), "guests", ["department"])
    op.create_index(op.f("ix_guests_status"), "guests", ["status"])
    # The token is the credential, and every guest request begins by looking it up.
    op.create_index(op.f("ix_guests_invite_token"), "guests", ["invite_token"], unique=True)

    op.create_table(
        "guest_jobs",
        sa.Column(
            "guest_id", UUID(as_uuid=True), sa.ForeignKey("guests.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("job_requirements.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("added_at", sa.TIMESTAMP(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("guest_jobs")
    op.drop_index(op.f("ix_guests_invite_token"), table_name="guests")
    op.drop_index(op.f("ix_guests_status"), table_name="guests")
    op.drop_index(op.f("ix_guests_department"), table_name="guests")
    op.drop_index(op.f("ix_guests_company_id"), table_name="guests")
    op.drop_table("guests")
    op.drop_table("user_group_roles")
    op.drop_table("user_group_members")
    op.drop_index(op.f("ix_user_groups_company_id"), table_name="user_groups")
    op.drop_table("user_groups")
