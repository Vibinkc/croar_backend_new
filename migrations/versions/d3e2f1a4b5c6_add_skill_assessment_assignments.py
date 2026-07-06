"""add skill_assessment_assignments (employee skill assessments)

Revision ID: d3e2f1a4b5c6
Revises: c7f1a9d2b3e4
Create Date: 2026-07-06

Employee-facing skill assessments: one row = one employee's assignment/sitting of an
existing assessment_templates definition.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d3e2f1a4b5c6"
down_revision = "c7f1a9d2b3e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_assessment_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "template_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assessment_templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "employee_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("answers", postgresql.JSONB(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("aptitude_score", sa.Integer(), nullable=True),
        sa.Column("coding_score", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="PENDING"),
        sa.Column("assigned_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_skill_assessment_assignments_employee_id",
        "skill_assessment_assignments",
        ["employee_id"],
    )
    op.create_index(
        "ix_skill_assessment_assignments_template_id",
        "skill_assessment_assignments",
        ["template_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_skill_assessment_assignments_template_id", table_name="skill_assessment_assignments")
    op.drop_index("ix_skill_assessment_assignments_employee_id", table_name="skill_assessment_assignments")
    op.drop_table("skill_assessment_assignments")
