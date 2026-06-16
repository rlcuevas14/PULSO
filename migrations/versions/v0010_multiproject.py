"""v0010: multiproject — projects table + project_id on all project-scoped tables"""

from alembic import op

revision = "v0010"
down_revision = "v0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE projects (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            slug                  TEXT UNIQUE NOT NULL,
            name                  TEXT NOT NULL,
            description           TEXT,
            color                 TEXT,
            repo_url              TEXT,
            github_webhook_secret TEXT,
            sentry_client_secret  TEXT,
            sentry_api_token      TEXT,
            sentry_org            TEXT,
            archived_at           TIMESTAMPTZ,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # Add project_id as nullable FK — NOT NULL enforced at app layer for now.
    # Existing rows (if any) must be assigned via the project management UI before
    # a future migration can add the NOT NULL constraint (v0011).
    for table in ("scopes", "items", "threads", "sentry_issues", "agent_runs"):
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN project_id UUID REFERENCES projects(id)"
        )
        op.execute(f"CREATE INDEX {table}_project ON {table}(project_id)")

    # api_tokens gets project_id too — token-per-project MCP isolation
    op.execute("ALTER TABLE api_tokens ADD COLUMN project_id UUID REFERENCES projects(id)")
    op.execute("CREATE INDEX api_tokens_project ON api_tokens(project_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS api_tokens_project")
    op.execute("ALTER TABLE api_tokens DROP COLUMN IF EXISTS project_id")

    for table in ("agent_runs", "sentry_issues", "threads", "items", "scopes"):
        op.execute(f"DROP INDEX IF EXISTS {table}_project")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS project_id")

    op.execute("DROP TABLE IF EXISTS projects")
