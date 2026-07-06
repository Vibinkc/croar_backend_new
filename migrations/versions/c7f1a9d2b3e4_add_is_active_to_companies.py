"""Add is_active to companies (tenant activate/deactivate)

Revision ID: c7f1a9d2b3e4
Revises: a1b2c3d4e5f6
Create Date: 2026-07-03 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7f1a9d2b3e4"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add companies.is_active (default true) so a tenant can be deactivated."""
    op.add_column(
        "companies",
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("companies", "is_active")
