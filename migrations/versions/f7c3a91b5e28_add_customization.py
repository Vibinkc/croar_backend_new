"""add custom fields, tags and customization settings

Definitions and values as two tables rather than a JSONB blob per record: a blob cannot answer
"which candidates have Notice Period set" without a full scan, cannot enforce that a select
holds one of its own options, and leaves orphaned keys behind forever when a field is deleted.

Revision ID: f7c3a91b5e28
Revises: e4b6f2d8a913
Create Date: 2026-09-09

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "f7c3a91b5e28"
down_revision: str | None = "e4b6f2d8a913"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "custom_field_definitions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("entity", sa.String(length=30), nullable=False),
        sa.Column("key", sa.String(length=60), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("field_type", sa.String(length=20), nullable=False, server_default="text"),
        sa.Column("options", JSONB, nullable=True),
        sa.Column("help_text", sa.Text(), nullable=True),
        sa.Column("required", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("show_on_create", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.TIMESTAMP(), nullable=True),
        sa.UniqueConstraint("company_id", "entity", "key", name="uq_custom_field_company_entity_key"),
    )
    op.create_index(op.f("ix_custom_field_definitions_entity"), "custom_field_definitions", ["entity"])
    op.create_index(op.f("ix_custom_field_definitions_company_id"), "custom_field_definitions", ["company_id"])

    op.create_table(
        "custom_field_values",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column(
            "definition_id",
            UUID(as_uuid=True),
            sa.ForeignKey("custom_field_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # No foreign key: record_id points at one of five tables depending on the definition's
        # entity, which Postgres cannot express. Company scope on every query keeps it honest.
        sa.Column("record_id", UUID(as_uuid=True), nullable=False),
        sa.Column("value", JSONB, nullable=True),
        sa.Column(
            "company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.func.now()),
        sa.UniqueConstraint("definition_id", "record_id", name="uq_custom_field_value_def_record"),
    )
    op.create_index(op.f("ix_custom_field_values_definition_id"), "custom_field_values", ["definition_id"])
    op.create_index(op.f("ix_custom_field_values_record_id"), "custom_field_values", ["record_id"])
    op.create_index(op.f("ix_custom_field_values_company_id"), "custom_field_values", ["company_id"])

    op.create_table(
        "tags",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("entity", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("colour", sa.String(length=7), nullable=True),
        sa.Column(
            "company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.TIMESTAMP(), nullable=True),
        sa.UniqueConstraint("company_id", "entity", "name", name="uq_tags_company_entity_name"),
    )
    op.create_index(op.f("ix_tags_entity"), "tags", ["entity"])
    op.create_index(op.f("ix_tags_company_id"), "tags", ["company_id"])

    op.create_table(
        "tag_assignments",
        sa.Column("tag_id", UUID(as_uuid=True), sa.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("record_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("assigned_at", sa.TIMESTAMP(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("tag_assignments")
    op.drop_index(op.f("ix_tags_company_id"), table_name="tags")
    op.drop_index(op.f("ix_tags_entity"), table_name="tags")
    op.drop_table("tags")
    op.drop_index(op.f("ix_custom_field_values_company_id"), table_name="custom_field_values")
    op.drop_index(op.f("ix_custom_field_values_record_id"), table_name="custom_field_values")
    op.drop_index(op.f("ix_custom_field_values_definition_id"), table_name="custom_field_values")
    op.drop_table("custom_field_values")
    op.drop_index(op.f("ix_custom_field_definitions_company_id"), table_name="custom_field_definitions")
    op.drop_index(op.f("ix_custom_field_definitions_entity"), table_name="custom_field_definitions")
    op.drop_table("custom_field_definitions")
