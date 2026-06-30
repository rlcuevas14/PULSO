"""v0015: relax project_id back to NULLABLE on the core tables.

PULSO (canonical) enforces project isolation in code (app/projects/access.py resolves and
filters every request by project), not via a schema NOT NULL. The upstream (now obsolete)
eduk3 line had hardened project_id to NOT NULL in v0013; this realigns the live schema with
PULSO's models (nullable project_id). A NULL project_id is simply an orphan row — invisible
to every project-scoped query, never leaked across accounts. The composite indexes from
v0013 are kept (harmless perf).
"""

from alembic import op

revision = "v0015"
down_revision = "v0014"
branch_labels = None
depends_on = None

_TABLES = ("scopes", "items", "threads", "sentry_issues", "agent_runs")


def upgrade() -> None:
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN project_id DROP NOT NULL")


def downgrade() -> None:
    # Re-harden: assign any orphan rows to the earliest project, then NOT NULL again.
    for table in _TABLES:
        op.execute(
            f"UPDATE {table} SET project_id = "
            f"(SELECT id FROM projects ORDER BY created_at LIMIT 1) "
            f"WHERE project_id IS NULL"
        )
        op.execute(f"ALTER TABLE {table} ALTER COLUMN project_id SET NOT NULL")
