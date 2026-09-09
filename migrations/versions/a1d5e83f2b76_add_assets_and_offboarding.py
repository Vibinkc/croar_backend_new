"""add assets and offboarding

The two ship together on purpose: an exit checklist that cannot name the laptop somebody is
holding is a to-do list, not an exit process. offboarding_tasks.asset_id is the join that makes
"return the MacBook" a row generated from the asset register rather than a line somebody
remembered to type.

Revision ID: a1d5e83f2b76
Revises: f7c3a91b5e28
Create Date: 2026-09-09

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "a1d5e83f2b76"
down_revision: str | None = "f7c3a91b5e28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("asset_tag", sa.String(length=60), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=30), nullable=False, server_default="other"),
        sa.Column("brand", sa.String(length=100), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("serial_number", sa.String(length=120), nullable=True),
        sa.Column("licence_key", sa.String(length=200), nullable=True),
        sa.Column("ownership", sa.String(length=20), nullable=False, server_default="company"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="available"),
        sa.Column("purchased_on", sa.TIMESTAMP(), nullable=True),
        sa.Column("purchase_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.TIMESTAMP(), nullable=True),
        # Per company: two companies will both have an "IT-001".
        sa.UniqueConstraint("company_id", "asset_tag", name="uq_assets_company_tag"),
    )
    op.create_index(op.f("ix_assets_company_id"), "assets", ["company_id"])
    op.create_index(op.f("ix_assets_category"), "assets", ["category"])
    op.create_index(op.f("ix_assets_status"), "assets", ["status"])

    op.create_table(
        "asset_assignments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("asset_id", UUID(as_uuid=True), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "employee_id", UUID(as_uuid=True), sa.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("assigned_at", sa.TIMESTAMP(), nullable=False, server_default=sa.func.now()),
        sa.Column("assigned_by", UUID(as_uuid=True), nullable=True),
        sa.Column("due_back_on", sa.TIMESTAMP(), nullable=True),
        sa.Column("returned_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("returned_to", UUID(as_uuid=True), nullable=True),
        sa.Column("return_condition", sa.String(length=20), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.func.now()),
    )
    op.create_index(op.f("ix_asset_assignments_asset_id"), "asset_assignments", ["asset_id"])
    op.create_index(op.f("ix_asset_assignments_employee_id"), "asset_assignments", ["employee_id"])
    op.create_index(op.f("ix_asset_assignments_company_id"), "asset_assignments", ["company_id"])
    # "What is currently out" is the most-asked question here, and it is a null test.
    op.create_index(op.f("ix_asset_assignments_returned_at"), "asset_assignments", ["returned_at"])

    op.create_table(
        "offboardings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column(
            "employee_id", UUID(as_uuid=True), sa.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("offboarding_type", sa.String(length=20), nullable=False),
        sa.Column("resignation_date", sa.TIMESTAMP(), nullable=True),
        sa.Column("last_working_day", sa.TIMESTAMP(), nullable=True),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("reason_other", sa.Text(), nullable=True),
        # Nullable on purpose: NULL means nobody decided, which is not "no".
        sa.Column("rehire_eligible", sa.Boolean(), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="requested"),
        sa.Column("requested_by", UUID(as_uuid=True), nullable=True),
        sa.Column("requested_at", sa.TIMESTAMP(), nullable=False, server_default=sa.func.now()),
        sa.Column("decided_by", UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(), nullable=True),
        sa.Column(
            "company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.TIMESTAMP(), nullable=True),
    )
    op.create_index(op.f("ix_offboardings_employee_id"), "offboardings", ["employee_id"])
    op.create_index(op.f("ix_offboardings_company_id"), "offboardings", ["company_id"])
    op.create_index(op.f("ix_offboardings_status"), "offboardings", ["status"])
    op.create_index(op.f("ix_offboardings_last_working_day"), "offboardings", ["last_working_day"])

    op.create_table(
        "offboarding_tasks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column(
            "offboarding_id",
            UUID(as_uuid=True),
            sa.ForeignKey("offboardings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=20), nullable=False, server_default="hr"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("owner_id", UUID(as_uuid=True), nullable=True),
        sa.Column("due_on", sa.TIMESTAMP(), nullable=True),
        # The join that makes the checklist and the asset register one thing.
        sa.Column("asset_id", UUID(as_uuid=True), sa.ForeignKey("assets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("required", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("done", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("done_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("done_by", UUID(as_uuid=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.func.now()),
    )
    op.create_index(op.f("ix_offboarding_tasks_offboarding_id"), "offboarding_tasks", ["offboarding_id"])
    op.create_index(op.f("ix_offboarding_tasks_category"), "offboarding_tasks", ["category"])
    op.create_index(op.f("ix_offboarding_tasks_company_id"), "offboarding_tasks", ["company_id"])


def downgrade() -> None:
    op.drop_table("offboarding_tasks")
    op.drop_index(op.f("ix_offboardings_last_working_day"), table_name="offboardings")
    op.drop_index(op.f("ix_offboardings_status"), table_name="offboardings")
    op.drop_index(op.f("ix_offboardings_company_id"), table_name="offboardings")
    op.drop_index(op.f("ix_offboardings_employee_id"), table_name="offboardings")
    op.drop_table("offboardings")
    op.drop_table("asset_assignments")
    op.drop_index(op.f("ix_assets_status"), table_name="assets")
    op.drop_index(op.f("ix_assets_category"), table_name="assets")
    op.drop_index(op.f("ix_assets_company_id"), table_name="assets")
    op.drop_table("assets")
