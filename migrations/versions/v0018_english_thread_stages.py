"""v0018: English thread stages + artifact kinds.

The last Spanish enum values in the public MCP contract (threads.stage,
thread_artifacts.stage, thread_artifacts.kind). Same policy as v0011: data is
migrated in place, no compatibility shim for old clients.
"""

from alembic import op

revision = "v0018"
down_revision = "v0017"
branch_labels = None
depends_on = None

STAGES = [
    ("investigacion", "research"),
    ("historias", "stories"),
    ("en-desarrollo", "in-development"),
    ("hecho", "done"),
    ("descartado", "discarded"),
]
KINDS = [
    ("investigacion", "research"),
    ("historias", "stories"),
    ("notas", "notes"),
]
_EN_STAGES = "'idea','research','stories','spec','in-development','review','done','discarded'"
_ES_STAGES = "'idea','investigacion','historias','spec','en-desarrollo','review','hecho','descartado'"
_EN_KINDS = "'research','stories','spec','notes','decision'"
_ES_KINDS = "'investigacion','historias','spec','notas','decision'"


def upgrade() -> None:
    # Drop the checks first — the renamed rows would violate them mid-flight.
    op.execute("ALTER TABLE threads DROP CONSTRAINT IF EXISTS threads_stage_check")
    op.execute("ALTER TABLE thread_artifacts DROP CONSTRAINT IF EXISTS thread_artifacts_stage_check")
    op.execute("ALTER TABLE thread_artifacts DROP CONSTRAINT IF EXISTS thread_artifacts_kind_check")
    for old, new in STAGES:
        op.execute(f"UPDATE threads SET stage = '{new}' WHERE stage = '{old}'")
        op.execute(f"UPDATE thread_artifacts SET stage = '{new}' WHERE stage = '{old}'")
    for old, new in KINDS:
        op.execute(f"UPDATE thread_artifacts SET kind = '{new}' WHERE kind = '{old}'")
    op.execute(f"ALTER TABLE threads ADD CONSTRAINT threads_stage_check CHECK (stage IN ({_EN_STAGES}))")
    op.execute(
        "ALTER TABLE thread_artifacts ADD CONSTRAINT thread_artifacts_stage_check "
        f"CHECK (stage IN ({_EN_STAGES}))"
    )
    op.execute(
        "ALTER TABLE thread_artifacts ADD CONSTRAINT thread_artifacts_kind_check "
        f"CHECK (kind IN ({_EN_KINDS}))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE threads DROP CONSTRAINT IF EXISTS threads_stage_check")
    op.execute("ALTER TABLE thread_artifacts DROP CONSTRAINT IF EXISTS thread_artifacts_stage_check")
    op.execute("ALTER TABLE thread_artifacts DROP CONSTRAINT IF EXISTS thread_artifacts_kind_check")
    for old, new in STAGES:
        op.execute(f"UPDATE threads SET stage = '{old}' WHERE stage = '{new}'")
        op.execute(f"UPDATE thread_artifacts SET stage = '{old}' WHERE stage = '{new}'")
    for old, new in KINDS:
        op.execute(f"UPDATE thread_artifacts SET kind = '{old}' WHERE kind = '{new}'")
    op.execute(f"ALTER TABLE threads ADD CONSTRAINT threads_stage_check CHECK (stage IN ({_ES_STAGES}))")
    op.execute(
        "ALTER TABLE thread_artifacts ADD CONSTRAINT thread_artifacts_stage_check "
        f"CHECK (stage IN ({_ES_STAGES}))"
    )
    op.execute(
        "ALTER TABLE thread_artifacts ADD CONSTRAINT thread_artifacts_kind_check "
        f"CHECK (kind IN ({_ES_KINDS}))"
    )
