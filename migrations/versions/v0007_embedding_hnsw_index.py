"""v0007: índice ANN (HNSW) para embeddings (PERF-02)

Acelera la búsqueda semántica por similitud de coseno sobre items.embedding.
Envuelto en un guard (igual que v0001 con pgvector): la columna `embedding` solo existe
si la extensión `vector` está instalada. En CI sí lo está; en dev local sin Docker no,
y este paso se omite graciosamente. El índice solo se crea si la columna existe.
"""

from alembic import op

revision = "v0007"
down_revision = "v0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'items' AND column_name = 'embedding'
            ) THEN
                CREATE INDEX IF NOT EXISTS items_embedding_hnsw
                ON items USING hnsw (embedding vector_cosine_ops);
            ELSE
                RAISE NOTICE 'columna items.embedding ausente — se omite el índice HNSW '
                             '(OK en dev local sin pgvector)';
            END IF;
        EXCEPTION WHEN undefined_object OR feature_not_supported THEN
            RAISE NOTICE 'pgvector/hnsw no disponible — se omite el índice HNSW '
                         '(OK en dev local sin pgvector)';
        END;
        $$
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS items_embedding_hnsw")
