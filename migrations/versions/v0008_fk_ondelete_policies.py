"""v0008: políticas ON DELETE en las FKs (DM-03)

Las FKs nacieron con NO ACTION implícito. Se redefinen con la política correcta:
  - CASCADE  para historial hijo de items (item_comments, item_events, ai_enrichments)
             y para thread_artifacts hijo de threads.
  - SET NULL para referencias opcionales (items.thread_id, threads.assignee_user_id,
             sentry_issues.item_id).
  - RESTRICT explícito para items.scope_id y threads.scope_id (no borrar un scope con ítems/hilos).

item_relationships ya nació CASCADE (v0003) → no se toca.

Cada FK se reconstruye con drop + add (Postgres no permite ALTER de la acción ON DELETE).
Los nombres siguen la convención de Postgres `<tabla>_<columna>_fkey`.
"""

from alembic import op

revision = "v0008"
down_revision = "v0007"
branch_labels = None
depends_on = None

# (constraint, tabla, columna, tabla_ref, columna_ref, ondelete_up, ondelete_down)
# ondelete_down = None significa recrear sin ON DELETE (NO ACTION, el estado original).
_FKS: list[tuple[str, str, str, str, str, str, str | None]] = [
    # Historial hijo de items -> CASCADE.
    ("item_comments_item_id_fkey", "item_comments", "item_id", "items", "id", "CASCADE", None),
    ("item_events_item_id_fkey", "item_events", "item_id", "items", "id", "CASCADE", None),
    ("ai_enrichments_item_id_fkey", "ai_enrichments", "item_id", "items", "id", "CASCADE", None),
    # thread_artifacts hijo de threads -> CASCADE.
    (
        "thread_artifacts_thread_id_fkey",
        "thread_artifacts",
        "thread_id",
        "threads",
        "id",
        "CASCADE",
        None,
    ),
    # Referencias opcionales -> SET NULL.
    ("items_thread_id_fkey", "items", "thread_id", "threads", "id", "SET NULL", None),
    (
        "threads_assignee_user_id_fkey",
        "threads",
        "assignee_user_id",
        "users",
        "id",
        "SET NULL",
        None,
    ),
    ("sentry_issues_item_id_fkey", "sentry_issues", "item_id", "items", "id", "SET NULL", None),
    # Scopes -> RESTRICT explícito.
    ("items_scope_id_fkey", "items", "scope_id", "scopes", "id", "RESTRICT", None),
    ("threads_scope_id_fkey", "threads", "scope_id", "scopes", "id", "RESTRICT", None),
]


def _recreate(
    constraint: str,
    table: str,
    column: str,
    ref_table: str,
    ref_column: str,
    ondelete: str | None,
) -> None:
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}")
    clause = f" ON DELETE {ondelete}" if ondelete else ""
    op.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT {constraint} "
        f"FOREIGN KEY ({column}) REFERENCES {ref_table}({ref_column}){clause}"
    )


def upgrade() -> None:
    for constraint, table, column, ref_table, ref_column, up, _down in _FKS:
        _recreate(constraint, table, column, ref_table, ref_column, up)


def downgrade() -> None:
    # Revierte a NO ACTION (estado original) para todas.
    for constraint, table, column, ref_table, ref_column, _up, down in _FKS:
        _recreate(constraint, table, column, ref_table, ref_column, down)
