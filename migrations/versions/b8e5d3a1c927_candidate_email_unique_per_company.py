"""candidates: email unique per company, not globally

The old constraint made a candidate's email unique across the whole table, which in a
multi-tenant database means the first company to record someone's address permanently blocks
every other company from ever adding them. Two agencies cannot both know the same developer.

It surfaced as a 500 on the Sourcing Hub's import: the duplicate check is company-scoped and
correctly finds nothing, so the insert proceeds and then trips a constraint that is not.

Scoping it to (company_id, email) is safe to apply as-is — under the old global rule no two
rows can already share an email, so no pair can violate the narrower one. NULL emails stay
unconstrained, which is what Postgres does with NULLs in a unique index anyway and what we
want: a sourced profile with no address is still a candidate.

Revision ID: b8e5d3a1c927
Revises: a7d4c2e9f018
Create Date: 2026-09-07

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8e5d3a1c927"
down_revision: Union[str, None] = "a7d4c2e9f018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The constraint is named by Postgres convention; drop defensively so this is re-runnable
    # against a database where it was already removed by hand.
    op.execute("ALTER TABLE candidates DROP CONSTRAINT IF EXISTS candidates_email_key")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_candidates_company_email "
        "ON candidates (company_id, email)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_candidates_company_email")
    # Only restorable while no email is shared across companies; if one is, this fails loudly
    # rather than silently discarding a row.
    op.execute("ALTER TABLE candidates ADD CONSTRAINT candidates_email_key UNIQUE (email)")
