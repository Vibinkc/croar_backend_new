"""add candidate GDPR consent

Consent as four columns rather than a boolean: status, when, how, and when it lapses. A regulator
asks all four, and true/false answers none of them.

Revision ID: d2a8c5b71e64
Revises: c9f1e7b4d305
Create Date: 2026-09-09

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d2a8c5b71e64"
down_revision: Union[str, None] = "c9f1e7b4d305"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("candidates", sa.Column("consent_status", sa.String(length=20), nullable=True))
    op.add_column("candidates", sa.Column("consent_at", sa.TIMESTAMP(), nullable=True))
    op.add_column("candidates", sa.Column("consent_source", sa.String(length=50), nullable=True))
    op.add_column("candidates", sa.Column("consent_expires_at", sa.TIMESTAMP(), nullable=True))
    op.add_column("candidates", sa.Column("consent_note", sa.Text(), nullable=True))
    # Existing candidates are left NULL on purpose. Backfilling them to "granted" would be
    # inventing a consent nobody gave; NULL says "never asked", which is the truth.
    op.create_index(op.f("ix_candidates_consent_status"), "candidates", ["consent_status"])


def downgrade() -> None:
    op.drop_index(op.f("ix_candidates_consent_status"), table_name="candidates")
    for col in ("consent_note", "consent_expires_at", "consent_source", "consent_at", "consent_status"):
        op.drop_column("candidates", col)
