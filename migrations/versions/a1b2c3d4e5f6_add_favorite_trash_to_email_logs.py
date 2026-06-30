"""add is_favorite and is_trashed to email_logs

Revision ID: a1b2c3d4e5f6
Revises: 92fd296f5505
Create Date: 2026-06-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '92fd296f5505'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema — add Favorites/Trash flags to email logs."""
    op.add_column(
        "email_logs",
        sa.Column("is_favorite", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "email_logs",
        sa.Column("is_trashed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("email_logs", "is_trashed")
    op.drop_column("email_logs", "is_favorite")
