"""v0005: hilos de desarrollo — threads + thread_artifacts + items.thread_id"""

from alembic import op

revision = "v0005"
down_revision = "v0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE threads (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            scope_id         UUID NOT NULL REFERENCES scopes(id),
            title            TEXT NOT NULL,
            summary_md       TEXT,
            stage            TEXT NOT NULL DEFAULT 'idea',
            assignee_user_id UUID REFERENCES users(id),
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT threads_stage_check CHECK (
                stage IN ('idea','investigacion','historias','spec','en-desarrollo','review','hecho','descartado'))
        )
    """)
    op.execute("""
        CREATE TABLE thread_artifacts (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            thread_id           UUID NOT NULL REFERENCES threads(id),
            stage               TEXT NOT NULL,
            kind                TEXT NOT NULL,
            content_md          TEXT NOT NULL,
            created_by_user_id  UUID REFERENCES users(id),
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT thread_artifacts_kind_check CHECK (
                kind IN ('investigacion','historias','spec','notas','decision'))
        )
    """)
    op.execute("CREATE INDEX thread_artifacts_thread ON thread_artifacts(thread_id)")
    op.execute("ALTER TABLE items ADD COLUMN thread_id UUID REFERENCES threads(id)")
    op.execute("CREATE INDEX items_thread ON items(thread_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS items_thread")
    op.execute("ALTER TABLE items DROP COLUMN IF EXISTS thread_id")
    op.execute("DROP TABLE IF EXISTS thread_artifacts")
    op.execute("DROP TABLE IF EXISTS threads")
