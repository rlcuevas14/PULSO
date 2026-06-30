"""v0012: backfill — proyecto por defecto para datos pre-existentes (single→multiproject)

Al migrar una instancia single-project a multiproyecto, v0010 agregó ``project_id``
NULLABLE sin asignar las filas existentes. Esta migración crea un proyecto GENÉRICO
``default`` y le asigna todas las filas huérfanas (``project_id IS NULL``). El nombre real
del proyecto lo pone el operador por la UI/DB — el código público se mantiene genérico.
Idempotente: ``ON CONFLICT DO NOTHING`` + ``WHERE project_id IS NULL``.
"""
from alembic import op

revision = "v0012"
down_revision = "v0011"
branch_labels = None
depends_on = None

# Tablas con project_id agregadas en v0010.
_TABLES = ("scopes", "items", "threads", "sentry_issues", "agent_runs", "api_tokens")


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO projects (slug, name, description)
        VALUES ('default', 'Default',
                'Proyecto creado al migrar de single-project a multiproyecto')
        ON CONFLICT (slug) DO NOTHING
        """
    )
    for table in _TABLES:
        op.execute(
            f"UPDATE {table} SET project_id = "
            f"(SELECT id FROM projects WHERE slug = 'default') "
            f"WHERE project_id IS NULL"
        )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(
            f"UPDATE {table} SET project_id = NULL "
            f"WHERE project_id = (SELECT id FROM projects WHERE slug = 'default')"
        )
    op.execute("DELETE FROM projects WHERE slug = 'default'")
