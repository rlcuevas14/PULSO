"""v0017: account-level Sentry connections + per-project slug routing.

sentry_connections (1:1 account) holds the webhook token + secrets. projects gain
sentry_project_slug (unique per account) and DROP the three vestigial per-project
sentry columns (written by the settings form since v0010 but never read by runtime
code). sentry_issues gain account_id so unmatched rows are tenancy-safe.
Spec: docs/superpowers/specs/2026-07-10-per-project-sentry-design.md
"""

from alembic import op

revision = "v0017"
down_revision = "v0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE sentry_connections (
            id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            account_id     uuid NOT NULL UNIQUE REFERENCES accounts(id) ON DELETE CASCADE,
            webhook_token  TEXT NOT NULL UNIQUE,
            client_secret  TEXT,
            api_token      TEXT,
            org_slug       TEXT,
            base_url       TEXT,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("ALTER TABLE projects ADD COLUMN sentry_project_slug TEXT")
    op.execute("""
        ALTER TABLE projects ADD CONSTRAINT projects_account_sentry_slug_uniq
        UNIQUE (account_id, sentry_project_slug)
    """)
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS sentry_client_secret")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS sentry_api_token")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS sentry_org")
    op.execute("""
        ALTER TABLE sentry_issues ADD COLUMN account_id uuid
        REFERENCES accounts(id) ON DELETE CASCADE
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE sentry_issues DROP COLUMN account_id")
    op.execute("ALTER TABLE projects DROP CONSTRAINT projects_account_sentry_slug_uniq")
    op.execute("ALTER TABLE projects DROP COLUMN sentry_project_slug")
    op.execute("ALTER TABLE projects ADD COLUMN sentry_client_secret TEXT")
    op.execute("ALTER TABLE projects ADD COLUMN sentry_api_token TEXT")
    op.execute("ALTER TABLE projects ADD COLUMN sentry_org TEXT")
    op.execute("DROP TABLE sentry_connections")
