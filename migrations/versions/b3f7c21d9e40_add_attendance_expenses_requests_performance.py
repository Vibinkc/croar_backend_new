"""add attendance, expenses, requests and performance

The last six post-hire gaps found by walking Oorwin, in one migration because they were built
together and share no ordering constraint beyond their own foreign keys.

Trimmed from an autogenerate run by hand. The autogenerate also surfaced a great deal of
unrelated drift between the models and the live schema — it wanted to drop
onboarding_documents.due_date among others — and none of that is included here. That drift is
real and worth fixing, but as a deliberate decision rather than a side effect of adding modules.

Revision ID: b3f7c21d9e40
Revises: a1d5e83f2b76
Create Date: 2026-09-09

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b3f7c21d9e40"
down_revision: str | None = "a1d5e83f2b76"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:

    op.create_table(
        "expense_categories",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("limit_amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("receipt_required", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "name", name="uq_expense_categories_company_name"),
    )

    op.create_index(
        op.f("ix_expense_categories_company_id"), "expense_categories", ["company_id"], unique=False
    )

    op.create_table(
        "objective_templates",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("measure", sa.String(length=20), server_default="percent", nullable=False),
        sa.Column("target_value", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("unit", sa.String(length=30), nullable=True),
        sa.Column("level", sa.String(length=20), server_default="individual", nullable=False),
        sa.Column("category", sa.String(length=80), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_objective_templates_company_id"), "objective_templates", ["company_id"], unique=False
    )

    op.create_table(
        "review_cycles",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("self_review_opens", sa.Date(), nullable=True),
        sa.Column("self_review_closes", sa.Date(), nullable=True),
        sa.Column("manager_review_opens", sa.Date(), nullable=True),
        sa.Column("manager_review_closes", sa.Date(), nullable=True),
        sa.Column("rating_max", sa.Integer(), server_default="5", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column("closed_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "name", name="uq_review_cycles_company_name"),
    )

    op.create_index(op.f("ix_review_cycles_company_id"), "review_cycles", ["company_id"], unique=False)

    op.create_index(op.f("ix_review_cycles_status"), "review_cycles", ["status"], unique=False)

    op.create_table(
        "shifts",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("starts_at", sa.Time(), nullable=False),
        sa.Column("ends_at", sa.Time(), nullable=False),
        sa.Column("break_minutes", sa.Integer(), server_default="60", nullable=False),
        sa.Column("half_day_after_minutes", sa.Integer(), server_default="240", nullable=False),
        sa.Column("full_day_after_minutes", sa.Integer(), server_default="480", nullable=False),
        sa.Column("working_days", sa.String(length=7), server_default="01234", nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "name", name="uq_shifts_company_name"),
    )

    op.create_index(op.f("ix_shifts_company_id"), "shifts", ["company_id"], unique=False)

    op.create_table(
        "ticket_categories",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("default_owner_id", sa.UUID(), nullable=True),
        sa.Column("target_hours", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "name", name="uq_ticket_categories_company_name"),
    )

    op.create_index(
        op.f("ix_ticket_categories_company_id"), "ticket_categories", ["company_id"], unique=False
    )

    op.create_table(
        "attendance_days",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("employee_id", sa.UUID(), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("shift_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="absent", nullable=False),
        sa.Column("work_mode", sa.String(length=20), nullable=True),
        sa.Column("source", sa.String(length=20), server_default="web", nullable=False),
        sa.Column("first_in", sa.TIMESTAMP(), nullable=True),
        sa.Column("last_out", sa.TIMESTAMP(), nullable=True),
        sa.Column("work_minutes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("late_minutes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("locked", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["shift_id"], ["shifts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("employee_id", "work_date", name="uq_attendance_employee_date"),
    )

    op.create_index(op.f("ix_attendance_days_company_id"), "attendance_days", ["company_id"], unique=False)

    op.create_index(op.f("ix_attendance_days_employee_id"), "attendance_days", ["employee_id"], unique=False)

    op.create_index(op.f("ix_attendance_days_status"), "attendance_days", ["status"], unique=False)

    op.create_index(op.f("ix_attendance_days_work_date"), "attendance_days", ["work_date"], unique=False)

    op.create_table(
        "attendance_regularizations",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("employee_id", sa.UUID(), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("requested_status", sa.String(length=20), nullable=False),
        sa.Column("requested_in", sa.TIMESTAMP(), nullable=True),
        sa.Column("requested_out", sa.TIMESTAMP(), nullable=True),
        sa.Column("requested_work_mode", sa.String(length=20), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("requested_by", sa.UUID(), nullable=True),
        sa.Column("requested_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_attendance_regularizations_company_id"),
        "attendance_regularizations",
        ["company_id"],
        unique=False,
    )

    op.create_index(
        op.f("ix_attendance_regularizations_employee_id"),
        "attendance_regularizations",
        ["employee_id"],
        unique=False,
    )

    op.create_index(
        op.f("ix_attendance_regularizations_status"), "attendance_regularizations", ["status"], unique=False
    )

    op.create_index(
        op.f("ix_attendance_regularizations_work_date"),
        "attendance_regularizations",
        ["work_date"],
        unique=False,
    )

    op.create_table(
        "change_requests",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("reference", sa.String(length=30), nullable=False),
        sa.Column("employee_id", sa.UUID(), nullable=False),
        sa.Column("field_name", sa.String(length=60), nullable=False),
        sa.Column("old_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("new_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("requested_by", sa.UUID(), nullable=True),
        sa.Column("requested_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(op.f("ix_change_requests_company_id"), "change_requests", ["company_id"], unique=False)

    op.create_index(op.f("ix_change_requests_employee_id"), "change_requests", ["employee_id"], unique=False)

    op.create_index(op.f("ix_change_requests_field_name"), "change_requests", ["field_name"], unique=False)

    op.create_index(op.f("ix_change_requests_status"), "change_requests", ["status"], unique=False)

    op.create_table(
        "expense_advances",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("reference", sa.String(length=30), nullable=False),
        sa.Column("employee_id", sa.UUID(), nullable=False),
        sa.Column("purpose", sa.String(length=300), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=8), server_default="INR", nullable=False),
        sa.Column("needed_by", sa.Date(), nullable=True),
        sa.Column(
            "settled_amount", sa.Numeric(precision=12, scale=2), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("status", sa.String(length=20), server_default="requested", nullable=False),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(op.f("ix_expense_advances_company_id"), "expense_advances", ["company_id"], unique=False)

    op.create_index(
        op.f("ix_expense_advances_employee_id"), "expense_advances", ["employee_id"], unique=False
    )

    op.create_index(op.f("ix_expense_advances_status"), "expense_advances", ["status"], unique=False)

    op.create_table(
        "objectives",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("cycle_id", sa.UUID(), nullable=False),
        sa.Column("employee_id", sa.UUID(), nullable=True),
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column("template_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("level", sa.String(length=20), server_default="individual", nullable=False),
        sa.Column("measure", sa.String(length=20), server_default="percent", nullable=False),
        sa.Column("target_value", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column(
            "current_value", sa.Numeric(precision=14, scale=2), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("unit", sa.String(length=30), nullable=True),
        sa.Column("weight", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="not_started", nullable=False),
        sa.Column("due_on", sa.Date(), nullable=True),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cycle_id"], ["review_cycles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["objectives.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["template_id"], ["objective_templates.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(op.f("ix_objectives_company_id"), "objectives", ["company_id"], unique=False)

    op.create_index(op.f("ix_objectives_cycle_id"), "objectives", ["cycle_id"], unique=False)

    op.create_index(op.f("ix_objectives_employee_id"), "objectives", ["employee_id"], unique=False)

    op.create_index(op.f("ix_objectives_level"), "objectives", ["level"], unique=False)

    op.create_index(op.f("ix_objectives_parent_id"), "objectives", ["parent_id"], unique=False)

    op.create_index(op.f("ix_objectives_status"), "objectives", ["status"], unique=False)

    op.create_table(
        "reviews",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("cycle_id", sa.UUID(), nullable=False),
        sa.Column("employee_id", sa.UUID(), nullable=False),
        sa.Column("reviewer_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="not_started", nullable=False),
        sa.Column("self_rating", sa.Numeric(precision=4, scale=2), nullable=True),
        sa.Column("self_comments", sa.Text(), nullable=True),
        sa.Column("self_submitted_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("manager_rating", sa.Numeric(precision=4, scale=2), nullable=True),
        sa.Column("manager_comments", sa.Text(), nullable=True),
        sa.Column("manager_submitted_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("shared_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("acknowledged_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("employee_response", sa.Text(), nullable=True),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cycle_id"], ["review_cycles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cycle_id", "employee_id", name="uq_reviews_cycle_employee"),
    )

    op.create_index(op.f("ix_reviews_company_id"), "reviews", ["company_id"], unique=False)

    op.create_index(op.f("ix_reviews_cycle_id"), "reviews", ["cycle_id"], unique=False)

    op.create_index(op.f("ix_reviews_employee_id"), "reviews", ["employee_id"], unique=False)

    op.create_index(op.f("ix_reviews_reviewer_id"), "reviews", ["reviewer_id"], unique=False)

    op.create_index(op.f("ix_reviews_status"), "reviews", ["status"], unique=False)

    op.create_table(
        "signature_requests",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("reference", sa.String(length=30), nullable=False),
        sa.Column("document_name", sa.String(length=200), nullable=False),
        sa.Column("document_path", sa.String(length=500), nullable=True),
        sa.Column("module", sa.String(length=20), server_default="other", nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=True),
        sa.Column("employee_id", sa.UUID(), nullable=True),
        sa.Column("signer_name", sa.String(length=200), nullable=False),
        sa.Column("signer_email", sa.String(length=254), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="awaiting", nullable=False),
        sa.Column("due_on", sa.Date(), nullable=True),
        sa.Column("access_token", sa.String(length=64), nullable=False),
        sa.Column("requested_by", sa.UUID(), nullable=True),
        sa.Column("requested_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("submitted_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("signed_name", sa.String(length=200), nullable=True),
        sa.Column("signed_ip", sa.String(length=45), nullable=True),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_signature_requests_access_token"), "signature_requests", ["access_token"], unique=True
    )

    op.create_index(
        op.f("ix_signature_requests_company_id"), "signature_requests", ["company_id"], unique=False
    )

    op.create_index(op.f("ix_signature_requests_due_on"), "signature_requests", ["due_on"], unique=False)

    op.create_index(
        op.f("ix_signature_requests_employee_id"), "signature_requests", ["employee_id"], unique=False
    )

    op.create_index(op.f("ix_signature_requests_module"), "signature_requests", ["module"], unique=False)

    op.create_index(op.f("ix_signature_requests_status"), "signature_requests", ["status"], unique=False)

    op.create_index(
        op.f("ix_signature_requests_subject_id"), "signature_requests", ["subject_id"], unique=False
    )

    op.create_table(
        "tickets",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("reference", sa.String(length=30), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("category_id", sa.UUID(), nullable=True),
        sa.Column("priority", sa.String(length=10), server_default="normal", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="open", nullable=False),
        sa.Column("raised_by", sa.UUID(), nullable=True),
        sa.Column("employee_id", sa.UUID(), nullable=True),
        sa.Column("assigned_to", sa.UUID(), nullable=True),
        sa.Column("due_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("resolved_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("closed_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["category_id"], ["ticket_categories.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(op.f("ix_tickets_assigned_to"), "tickets", ["assigned_to"], unique=False)

    op.create_index(op.f("ix_tickets_category_id"), "tickets", ["category_id"], unique=False)

    op.create_index(op.f("ix_tickets_company_id"), "tickets", ["company_id"], unique=False)

    op.create_index(op.f("ix_tickets_employee_id"), "tickets", ["employee_id"], unique=False)

    op.create_index(op.f("ix_tickets_priority"), "tickets", ["priority"], unique=False)

    op.create_index(op.f("ix_tickets_raised_by"), "tickets", ["raised_by"], unique=False)

    op.create_index(op.f("ix_tickets_status"), "tickets", ["status"], unique=False)

    op.create_table(
        "attendance_punches",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("day_id", sa.UUID(), nullable=False),
        sa.Column("direction", sa.String(length=4), nullable=False),
        sa.Column("punched_at", sa.TIMESTAMP(), nullable=False),
        sa.Column("source", sa.String(length=20), server_default="web", nullable=False),
        sa.Column("location", sa.String(length=120), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["day_id"], ["attendance_days.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_attendance_punches_company_id"), "attendance_punches", ["company_id"], unique=False
    )

    op.create_index(op.f("ix_attendance_punches_day_id"), "attendance_punches", ["day_id"], unique=False)

    op.create_table(
        "expense_claims",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("reference", sa.String(length=30), nullable=False),
        sa.Column("employee_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("currency", sa.String(length=8), server_default="INR", nullable=False),
        sa.Column(
            "total_amount", sa.Numeric(precision=12, scale=2), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("approved_amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column("submitted_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("advance_id", sa.UUID(), nullable=True),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["advance_id"], ["expense_advances.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(op.f("ix_expense_claims_company_id"), "expense_claims", ["company_id"], unique=False)

    op.create_index(op.f("ix_expense_claims_employee_id"), "expense_claims", ["employee_id"], unique=False)

    op.create_index(op.f("ix_expense_claims_status"), "expense_claims", ["status"], unique=False)

    op.create_table(
        "objective_checkins",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("objective_id", sa.UUID(), nullable=False),
        sa.Column("value", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("author_id", sa.UUID(), nullable=True),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["objective_id"], ["objectives.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_objective_checkins_company_id"), "objective_checkins", ["company_id"], unique=False
    )

    op.create_index(
        op.f("ix_objective_checkins_objective_id"), "objective_checkins", ["objective_id"], unique=False
    )

    op.create_table(
        "ticket_comments",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("ticket_id", sa.UUID(), nullable=False),
        sa.Column("author_id", sa.UUID(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("internal", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(op.f("ix_ticket_comments_company_id"), "ticket_comments", ["company_id"], unique=False)

    op.create_index(op.f("ix_ticket_comments_ticket_id"), "ticket_comments", ["ticket_id"], unique=False)

    op.create_table(
        "expense_items",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("claim_id", sa.UUID(), nullable=False),
        sa.Column("category_id", sa.UUID(), nullable=True),
        sa.Column("spent_on", sa.Date(), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("merchant", sa.String(length=160), nullable=True),
        sa.Column("receipt_path", sa.String(length=500), nullable=True),
        sa.Column("approved_amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["expense_categories.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["claim_id"], ["expense_claims.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(op.f("ix_expense_items_claim_id"), "expense_items", ["claim_id"], unique=False)

    op.create_index(op.f("ix_expense_items_company_id"), "expense_items", ["company_id"], unique=False)

    op.create_table(
        "expense_payments",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("claim_id", sa.UUID(), nullable=True),
        sa.Column("advance_id", sa.UUID(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("method", sa.String(length=20), server_default="bank_transfer", nullable=False),
        sa.Column("reference", sa.String(length=120), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("paid_by", sa.UUID(), nullable=True),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["advance_id"], ["expense_advances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["claim_id"], ["expense_claims.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(op.f("ix_expense_payments_advance_id"), "expense_payments", ["advance_id"], unique=False)

    op.create_index(op.f("ix_expense_payments_claim_id"), "expense_payments", ["claim_id"], unique=False)

    op.create_index(op.f("ix_expense_payments_company_id"), "expense_payments", ["company_id"], unique=False)


def downgrade() -> None:
    op.drop_table("reviews")
    op.drop_table("objective_checkins")
    op.drop_table("objectives")
    op.drop_table("objective_templates")
    op.drop_table("review_cycles")
    op.drop_table("change_requests")
    op.drop_table("ticket_comments")
    op.drop_table("tickets")
    op.drop_table("ticket_categories")
    op.drop_table("signature_requests")
    op.drop_table("expense_payments")
    op.drop_table("expense_items")
    op.drop_table("expense_claims")
    op.drop_table("expense_advances")
    op.drop_table("expense_categories")
    op.drop_table("attendance_regularizations")
    op.drop_table("attendance_punches")
    op.drop_table("attendance_days")
    op.drop_table("shifts")
