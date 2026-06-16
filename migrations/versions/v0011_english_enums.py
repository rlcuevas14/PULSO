"""v0011: English enums — rename Spanish status/type/origen/comment-kind values"""

from alembic import op

revision = "v0011"
down_revision = "v0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # items.status
    op.execute("ALTER TABLE items DROP CONSTRAINT IF EXISTS items_status_check")
    for old, new in [
        ("en-curso", "in-progress"),
        ("bloqueado", "blocked"),
        ("en-revision", "in-review"),
        ("hecho", "done"),
        ("descartado", "discarded"),
    ]:
        op.execute(f"UPDATE items SET status = '{new}' WHERE status = '{old}'")
    op.execute(
        "ALTER TABLE items ADD CONSTRAINT items_status_check CHECK ("
        "status IN ('idea','backlog','spec','in-progress','blocked','in-review','done','discarded'))"
    )

    # items.type
    op.execute("ALTER TABLE items DROP CONSTRAINT IF EXISTS items_type_check")
    op.execute("UPDATE items SET type = 'security' WHERE type = 'seguridad'")
    op.execute("UPDATE items SET type = 'product' WHERE type = 'producto'")
    op.execute(
        "ALTER TABLE items ADD CONSTRAINT items_type_check CHECK ("
        "type IN ('bug','feature','tech-debt','infra','docs','ops','security','product','idea'))"
    )

    # items.origen
    op.execute("ALTER TABLE items DROP CONSTRAINT IF EXISTS items_origen_check")
    for old, new in [("humano", "human"), ("ia-sesion", "ai-session"), ("agente", "agent")]:
        op.execute(f"UPDATE items SET origen = '{new}' WHERE origen = '{old}'")
    op.execute(
        "ALTER TABLE items ADD CONSTRAINT items_origen_check CHECK ("
        "origen IN ('digest','human','ai-session','sentry','agent'))"
    )

    # item_comments.kind
    op.execute("ALTER TABLE item_comments DROP CONSTRAINT IF EXISTS item_comments_kind_check")
    for old, new in [
        ("comentario", "comment"),
        ("analisis-ia", "ai-analysis"),
        ("cambio-estado", "status-change"),
    ]:
        op.execute(f"UPDATE item_comments SET kind = '{new}' WHERE kind = '{old}'")
    op.execute(
        "ALTER TABLE item_comments ADD CONSTRAINT item_comments_kind_check CHECK ("
        "kind IN ('comment','ai-analysis','decision','status-change'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE item_comments DROP CONSTRAINT IF EXISTS item_comments_kind_check")
    for new, old in [
        ("comment", "comentario"), ("ai-analysis", "analisis-ia"), ("status-change", "cambio-estado")
    ]:
        op.execute(f"UPDATE item_comments SET kind = '{old}' WHERE kind = '{new}'")
    op.execute(
        "ALTER TABLE item_comments ADD CONSTRAINT item_comments_kind_check CHECK ("
        "kind IN ('comentario','analisis-ia','decision','cambio-estado'))"
    )

    op.execute("ALTER TABLE items DROP CONSTRAINT IF EXISTS items_origen_check")
    for new, old in [("human", "humano"), ("ai-session", "ia-sesion"), ("agent", "agente")]:
        op.execute(f"UPDATE items SET origen = '{old}' WHERE origen = '{new}'")
    op.execute(
        "ALTER TABLE items ADD CONSTRAINT items_origen_check CHECK ("
        "origen IN ('digest','humano','ia-sesion','sentry','agente'))"
    )

    op.execute("ALTER TABLE items DROP CONSTRAINT IF EXISTS items_type_check")
    op.execute("UPDATE items SET type = 'seguridad' WHERE type = 'security'")
    op.execute("UPDATE items SET type = 'producto' WHERE type = 'product'")
    op.execute(
        "ALTER TABLE items ADD CONSTRAINT items_type_check CHECK ("
        "type IN ('bug','feature','tech-debt','infra','docs','ops','seguridad','producto','idea'))"
    )

    op.execute("ALTER TABLE items DROP CONSTRAINT IF EXISTS items_status_check")
    for new, old in [
        ("in-progress", "en-curso"), ("blocked", "bloqueado"),
        ("in-review", "en-revision"), ("done", "hecho"), ("discarded", "descartado"),
    ]:
        op.execute(f"UPDATE items SET status = '{old}' WHERE status = '{new}'")
    op.execute(
        "ALTER TABLE items ADD CONSTRAINT items_status_check CHECK ("
        "status IN ('idea','backlog','spec','en-curso','bloqueado','en-revision','hecho','descartado'))"
    )
