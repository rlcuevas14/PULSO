"""v0006: índices btree faltantes (PERF-01, DM-04)

Cubre los patrones de acceso reales: filtros por estado/scope, orden por fecha e
impacto, índices parciales sobre ítems abiertos, y FKs de tablas hijas sin índice.
"""

from alembic import op

from app.enums import TERMINAL, sql_list

revision = "v0006"
down_revision = "v0005"
branch_labels = None
depends_on = None

_TERMINAL_SQL = sql_list(TERMINAL)  # "'hecho','descartado'"

# (nombre_indice, sentencia CREATE INDEX). El downgrade hace DROP en orden inverso.
_INDEXES: list[tuple[str, str]] = [
    ("items_status_idx", "CREATE INDEX IF NOT EXISTS items_status_idx ON items (status)"),
    ("items_scope_id_idx", "CREATE INDEX IF NOT EXISTS items_scope_id_idx ON items (scope_id)"),
    (
        "items_created_at_idx",
        "CREATE INDEX IF NOT EXISTS items_created_at_idx ON items (created_at DESC)",
    ),
    (
        "items_scope_open_idx",
        "CREATE INDEX IF NOT EXISTS items_scope_open_idx ON items (scope_id, status) "
        f"WHERE status NOT IN ({_TERMINAL_SQL})",
    ),
    (
        "items_impact_open_idx",
        "CREATE INDEX IF NOT EXISTS items_impact_open_idx ON items (impact_ai DESC) "
        f"WHERE status NOT IN ({_TERMINAL_SQL})",
    ),
    (
        "item_comments_item_idx",
        "CREATE INDEX IF NOT EXISTS item_comments_item_idx ON item_comments (item_id)",
    ),
    (
        "item_events_item_idx",
        "CREATE INDEX IF NOT EXISTS item_events_item_idx ON item_events (item_id)",
    ),
    (
        "ai_enrichments_item_idx",
        "CREATE INDEX IF NOT EXISTS ai_enrichments_item_idx ON ai_enrichments (item_id)",
    ),
    (
        "sentry_issues_item_idx",
        "CREATE INDEX IF NOT EXISTS sentry_issues_item_idx ON sentry_issues (item_id)",
    ),
    (
        "sentry_issues_status_lastseen_idx",
        "CREATE INDEX IF NOT EXISTS sentry_issues_status_lastseen_idx "
        "ON sentry_issues (status, last_seen)",
    ),
    (
        "agent_runs_status_created_idx",
        "CREATE INDEX IF NOT EXISTS agent_runs_status_created_idx "
        "ON agent_runs (status, created_at)",
    ),
    (
        "agent_runs_status_lease_idx",
        "CREATE INDEX IF NOT EXISTS agent_runs_status_lease_idx "
        "ON agent_runs (status, leased_until)",
    ),
]


def upgrade() -> None:
    for _name, ddl in _INDEXES:
        op.execute(ddl)


def downgrade() -> None:
    for name, _ddl in reversed(_INDEXES):
        op.execute(f"DROP INDEX IF EXISTS {name}")
