"""add auto_move to assessment_automations

Revision ID: 0b5336d2e261
Revises: 0baa7e3a50cc
Create Date: 2026-03-21 12:19:42.016666

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0b5336d2e261'
down_revision: Union[str, None] = '0baa7e3a50cc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('assessment_automations', sa.Column('auto_move', sa.Boolean(), server_default=sa.text('false'), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('assessment_automations', 'auto_move')
