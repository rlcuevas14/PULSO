"""v0002: full-text search — tsvector generado + índice GIN"""

import sqlalchemy as sa
from alembic import op

revision = "v0002"
down_revision = "v0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE items
        ADD COLUMN search_vector tsvector
        GENERATED ALWAYS AS (
            setweight(to_tsvector('spanish', coalesce(title, '')), 'A') ||
            setweight(to_tsvector('spanish', coalesce(summary_md, '')), 'B')
        ) STORED
    """)
    op.execute("CREATE INDEX items_search_gin ON items USING GIN (search_vector)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS items_search_gin")
    op.execute("ALTER TABLE items DROP COLUMN IF EXISTS search_vector")
