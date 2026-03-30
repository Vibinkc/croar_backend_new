"""add project tasks and kanban columns

Revision ID: 611a8b3484ca
Revises: b6efb54de7d2
Create Date: 2026-03-26 16:01:26.472023

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '611a8b3484ca'
down_revision: Union[str, None] = 'b6efb54de7d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create project_tasks table
    op.create_table('project_tasks',
        sa.Column('id', sa.UUID(), server_default=sa.text('uuid_generate_v4()'), nullable=False),
        sa.Column('project_id', sa.UUID(), nullable=False),
        sa.Column('employee_id', sa.UUID(), nullable=True),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('column', sa.String(length=100), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='Pending'),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Add kanban_columns to projects table
    op.add_column('projects', sa.Column('kanban_columns', postgresql.JSONB(astext_type=sa.Text()), 
        server_default=sa.text('\'["Planning", "Development", "Testing", "Done"]\'::jsonb'), nullable=False))


def downgrade() -> None:
    op.drop_column('projects', 'kanban_columns')
    op.drop_table('project_tasks')
