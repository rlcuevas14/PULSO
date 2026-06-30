"""v0013: hardening de aislamiento por proyecto

Convierte el aislamiento de "disciplina de código" en garantía de schema:

1. `scopes.name` deja de ser UNIQUE GLOBAL y pasa a UNIQUE(project_id, name) — hoy el
   2º proyecto que crea un área de nombre común ("infra", "backend") choca con un 500.
2. `project_id` pasa a NOT NULL en las tablas core (tras backfill defensivo de cualquier
   huérfano que haya quedado — p. ej. incidentes Sentry pre-fix de webhooks). `api_tokens`
   se deja nullable a propósito (el runtime ya rechaza tokens sin proyecto).
3. Índices compuestos para que el scoping no degrade a seq-scan al crecer a N proyectos.

NOTA: el UNIQUE(project_id, sentry_issue_id) se difiere — requiere volver el dedup de
`ingest_sentry` project-aware; hoy un solo Sentry → un proyecto, así que el global alcanza.
"""
from alembic import op

revision = "v0013"
down_revision = "v0012"
branch_labels = None
depends_on = None

# Tablas core cuyo project_id pasa a NOT NULL (api_tokens queda nullable a propósito).
_NOT_NULL_TABLES = ("scopes", "items", "threads", "sentry_issues", "agent_runs")


def upgrade() -> None:
    # --- 1. scopes.name: UNIQUE global → UNIQUE(project_id, name) ---
    op.drop_constraint("scopes_name_key", "scopes", type_="unique")
    op.create_unique_constraint("scopes_project_name_key", "scopes", ["project_id", "name"])

    # --- 2. Backfill defensivo de huérfanos + project_id NOT NULL ---
    # Cualquier fila que haya quedado con project_id NULL (p. ej. incidentes Sentry
    # ingeridos antes del fix de webhooks) se asigna al primer proyecto existente.
    for table in _NOT_NULL_TABLES:
        op.execute(
            f"UPDATE {table} SET project_id = "
            f"(SELECT id FROM projects ORDER BY created_at LIMIT 1) "
            f"WHERE project_id IS NULL"
        )
        op.alter_column(table, "project_id", nullable=False)

    # --- 3. Índices compuestos para el scoping ---
    op.create_index("items_project_status_idx", "items", ["project_id", "status"])
    op.create_index(
        "items_project_scope_open_idx", "items", ["project_id", "scope_id"],
        postgresql_where="status NOT IN ('done','discarded')",
    )
    op.create_index(
        "sentry_project_status_seen_idx", "sentry_issues",
        ["project_id", "status", "last_seen"],
    )
    op.create_index("threads_project_stage_idx", "threads", ["project_id", "stage"])


def downgrade() -> None:
    op.drop_index("threads_project_stage_idx", "threads")
    op.drop_index("sentry_project_status_seen_idx", "sentry_issues")
    op.drop_index("items_project_scope_open_idx", "items")
    op.drop_index("items_project_status_idx", "items")

    for table in _NOT_NULL_TABLES:
        op.alter_column(table, "project_id", nullable=True)

    op.drop_constraint("scopes_project_name_key", "scopes", type_="unique")
    op.create_unique_constraint("scopes_name_key", "scopes", ["name"])
