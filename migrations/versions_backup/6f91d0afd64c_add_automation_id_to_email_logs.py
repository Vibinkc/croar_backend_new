"""add automation_id to email_logs

Revision ID: 6f91d0afd64c
Revises: 1674ce399dd5
Create Date: 2026-03-21 11:32:28.043504

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6f91d0afd64c'
down_revision: Union[str, None] = '1674ce399dd5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('email_logs', sa.Column('automation_id', sa.UUID(), sa.ForeignKey('mail_automations.id', ondelete='SET NULL'), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('email_logs', 'automation_id')
