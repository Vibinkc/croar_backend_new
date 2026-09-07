"""add candidate folders

Recruiter-owned grouping of candidates, independent of any job pipeline.

Revision ID: a7d4c2e9f018
Revises: e4f3a2b1c0d9
Create Date: 2026-09-07

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7d4c2e9f018"
down_revision: Union[str, None] = "e4f3a2b1c0d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "candidate_folders",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("company_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "name", name="uq_candidate_folders_company_name"),
    )
    op.create_index(op.f("ix_candidate_folders_company_id"), "candidate_folders", ["company_id"])

    op.create_table(
        "candidate_folder_members",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("folder_id", sa.UUID(), nullable=False),
        sa.Column("candidate_id", sa.UUID(), nullable=False),
        sa.Column("added_by", sa.UUID(), nullable=True),
        sa.Column("added_at", sa.TIMESTAMP(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["folder_id"], ["candidate_folders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("folder_id", "candidate_id", name="uq_candidate_folder_members"),
    )
    op.create_index(op.f("ix_candidate_folder_members_folder_id"), "candidate_folder_members", ["folder_id"])
    op.create_index(op.f("ix_candidate_folder_members_candidate_id"), "candidate_folder_members", ["candidate_id"])


def downgrade() -> None:
    op.drop_table("candidate_folder_members")
    op.drop_table("candidate_folders")
