"""v0009: api_tokens.expires_at (SEC-03) + thread_artifacts.stage CHECK (DM-05)

- SEC-03: columna opcional de expiración para tokens de API (NULL = sin expiración).
- DM-05:  thread_artifacts.stage carecía de CHECK; se alinea con el dominio de threads.stage.
"""

from alembic import op

from app.enums import THREAD_STAGES, check_in

revision = "v0009"
down_revision = "v0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE api_tokens ADD COLUMN expires_at TIMESTAMPTZ")
    op.execute(
        "ALTER TABLE thread_artifacts ADD CONSTRAINT thread_artifacts_stage_check "
        f"CHECK ({check_in('stage', THREAD_STAGES)})"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE thread_artifacts DROP CONSTRAINT IF EXISTS thread_artifacts_stage_check"
    )
    op.execute("ALTER TABLE api_tokens DROP COLUMN IF EXISTS expires_at")
