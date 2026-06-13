"""v0004: items.last_touched_at + source_refs -> JSONB (dedup eficiente de webhooks)"""

from alembic import op

revision = "v0004"
down_revision = "v0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE items ADD COLUMN last_touched_at TIMESTAMPTZ")
    # source_refs nació como JSON (texto); JSONB permite operadores e índice GIN
    # para la dedup de Sentry por fingerprint.
    op.execute("ALTER TABLE items ALTER COLUMN source_refs TYPE JSONB USING source_refs::jsonb")
    op.execute("CREATE INDEX items_source_refs_gin ON items USING GIN (source_refs)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS items_source_refs_gin")
    op.execute("ALTER TABLE items ALTER COLUMN source_refs TYPE JSON USING source_refs::json")
    op.execute("ALTER TABLE items DROP COLUMN IF EXISTS last_touched_at")
