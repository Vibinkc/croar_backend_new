"""Employee uniqueness applies to live rows only

A flat UNIQUE(company_id, email) counts soft-deleted rows, so archiving an employee held their
address and employee code hostage for good: the same person could never be re-added after
leaving and returning, and no API change could work around a database constraint.

Replaces both constraints with partial unique indexes carrying WHERE deleted_at IS NULL, which
is the convention already used on salary_structures.

Note the drop order. The constraints and the new indexes share their names, so the old ones have
to go before the new ones are created.

Revision ID: c4a9e2f6b183
Revises: b3f7c21d9e40
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c4a9e2f6b183"
down_revision: str | Sequence[str] | None = "b3f7c21d9e40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Archived duplicates may already exist and would fail the new index. Nothing here creates
    # them — the old constraint prevented it — so a plain create is safe.
    op.drop_constraint("uq_employees_company_email", "employees", type_="unique")
    op.drop_constraint("uq_employees_company_employee_id", "employees", type_="unique")

    op.create_index(
        "uq_employees_company_email",
        "employees",
        ["company_id", "email"],
        unique=True,
        postgresql_where="deleted_at IS NULL",
    )
    op.create_index(
        "uq_employees_company_employee_id",
        "employees",
        ["company_id", "employee_id"],
        unique=True,
        postgresql_where="deleted_at IS NULL",
    )


def downgrade() -> None:
    # Going back can genuinely fail: if an archived row now shares an email with a live one,
    # the flat constraint has no way to hold. That is a real conflict rather than a bug here,
    # and the data has to be reconciled before downgrading.
    op.drop_index("uq_employees_company_employee_id", table_name="employees")
    op.drop_index("uq_employees_company_email", table_name="employees")

    op.create_unique_constraint(
        "uq_employees_company_employee_id", "employees", ["company_id", "employee_id"]
    )
    op.create_unique_constraint("uq_employees_company_email", "employees", ["company_id", "email"])
