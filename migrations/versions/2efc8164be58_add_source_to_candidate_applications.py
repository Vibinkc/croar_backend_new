"""add_source_to_candidate_applications

Revision ID: 2efc8164be58
Revises: 0043481c537a
Create Date: 2026-05-21 11:09:22.483431

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2efc8164be58'
down_revision: Union[str, None] = '0043481c537a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspect_result = sa.inspect(bind)
    columns = [c['name'] for c in inspect_result.get_columns('candidate_applications')]
    if 'source' not in columns:
        op.add_column('candidate_applications', sa.Column('source', sa.String(length=50), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspect_result = sa.inspect(bind)
    columns = [c['name'] for c in inspect_result.get_columns('candidate_applications')]
    if 'source' in columns:
        op.drop_column('candidate_applications', 'source')
