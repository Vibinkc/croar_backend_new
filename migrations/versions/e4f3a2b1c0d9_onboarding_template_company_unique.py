"""onboarding_templates: unique per (company_id, name), not globally on name.

Revision ID: e4f3a2b1c0d9
Revises: d3e2f1a4b5c6
Create Date: 2026-07-14

The `onboarding_templates.name` column had a GLOBAL unique constraint. That's wrong for a
multi-tenant app: once one company created e.g. "UI/UX Designer · Onboarding", every OTHER
company's Croar Pilot pipeline build failed with a duplicate-key error (the per-company
find-or-create missed, then the insert hit the global unique) and the whole pipeline rolled back.

Swap the global UNIQUE(name) for a composite UNIQUE(company_id, name). Written with IF EXISTS /
existence guards so it's safe to run whether or not the change was already applied by hand.
"""

from alembic import op

revision = "e4f3a2b1c0d9"
down_revision = "d3e2f1a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE onboarding_templates DROP CONSTRAINT IF EXISTS onboarding_templates_name_key")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'uq_onboarding_templates_company_name'
            ) THEN
                ALTER TABLE onboarding_templates
                    ADD CONSTRAINT uq_onboarding_templates_company_name UNIQUE (company_id, name);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE onboarding_templates DROP CONSTRAINT IF EXISTS uq_onboarding_templates_company_name")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'onboarding_templates_name_key'
            ) THEN
                ALTER TABLE onboarding_templates
                    ADD CONSTRAINT onboarding_templates_name_key UNIQUE (name);
            END IF;
        END $$;
        """
    )
