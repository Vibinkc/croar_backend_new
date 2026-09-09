"""add activities

Scheduled calls, meetings and interviews — Manatal's Activities screen. Distinct from
`job_activities`, which is an audit log of what already happened.

Revision ID: c9f1e7b4d305
Revises: b8e5d3a1c927
Create Date: 2026-09-09

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9f1e7b4d305"
down_revision: Union[str, None] = "b8e5d3a1c927"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "activities",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("activity_type", sa.String(length=30), nullable=False, server_default="other"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("starts_at", sa.TIMESTAMP(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("candidate_id", sa.UUID(), nullable=True),
        sa.Column("job_requirement_id", sa.UUID(), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("company_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_requirement_id"], ["job_requirements.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_activities_starts_at"), "activities", ["starts_at"])
    op.create_index(op.f("ix_activities_company_id"), "activities", ["company_id"])
    op.create_index(op.f("ix_activities_candidate_id"), "activities", ["candidate_id"])
    op.create_index(op.f("ix_activities_job_requirement_id"), "activities", ["job_requirement_id"])

    op.create_table(
        "activity_assignees",
        sa.Column("activity_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["activity_id"], ["activities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("activity_id", "user_id"),
    )


def downgrade() -> None:
    op.drop_table("activity_assignees")
    op.drop_table("activities")
