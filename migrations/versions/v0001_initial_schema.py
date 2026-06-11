"""v0001: schema inicial — 9 tablas"""

import sqlalchemy as sa
from alembic import op

revision = "v0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Instalar pgvector si está disponible; en dev local sin Docker se omite graciosamente
    op.execute("""
        DO $$
        BEGIN
            CREATE EXTENSION IF NOT EXISTS vector;
        EXCEPTION WHEN feature_not_supported OR undefined_file THEN
            RAISE NOTICE 'pgvector extension not available — skipping (OK in local dev without Docker)';
        END;
        $$
    """)

    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default=sa.text("'viewer'")),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.CheckConstraint("role IN ('admin','viewer')", name="users_role_check"),
    )

    op.create_table(
        "api_tokens",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("scopes", sa.String(20), nullable=False, server_default=sa.text("'read'")),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
        sa.CheckConstraint("scopes IN ('read','write')", name="api_tokens_scopes_check"),
    )

    op.create_table(
        "scopes",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("color", sa.String(7), nullable=True),
        sa.Column("source_repo", sa.String(60), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "items",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("scope_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("summary_md", sa.Text(), nullable=True),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'backlog'")),
        sa.Column("priority", sa.String(5), nullable=True),
        sa.Column("effort_ai", sa.String(5), nullable=True),
        sa.Column("impact_ai", sa.SmallInteger(), nullable=True),
        sa.Column("impact_rationale", sa.Text(), nullable=True),
        sa.Column("effort_declared", sa.Text(), nullable=True),
        sa.Column("priority_declared", sa.Text(), nullable=True),
        sa.Column("trigger_text", sa.Text(), nullable=True),   # "trigger" es reservado en Python
        sa.Column("dependencies", sa.Text(), nullable=True),
        sa.Column("origen", sa.String(20), nullable=False, server_default=sa.text("'humano'")),
        sa.Column("source_refs", sa.JSON(), nullable=True),
        sa.Column("stale_risk", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("agent_ready", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("closed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["scope_id"], ["scopes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "type IN ('bug','feature','tech-debt','infra','docs','ops','seguridad','producto','idea')",
            name="items_type_check",
        ),
        sa.CheckConstraint(
            "status IN ('idea','backlog','spec','en-curso','bloqueado','en-revision','hecho','descartado')",
            name="items_status_check",
        ),
        sa.CheckConstraint(
            "priority IS NULL OR priority IN ('p0','p1','p2','p3')",
            name="items_priority_check",
        ),
        sa.CheckConstraint(
            "effort_ai IS NULL OR effort_ai IN ('XS','S','M','L','XL')",
            name="items_effort_ai_check",
        ),
        sa.CheckConstraint(
            "origen IN ('digest','humano','ia-sesion','sentry','agente')",
            name="items_origen_check",
        ),
    )
    # vector(768) requiere SQL raw — pgvector no es tipo SA nativo en Alembic
    # Se omite graciosamente si pgvector no está instalado (dev local sin Docker)
    op.execute("""
        DO $$
        BEGIN
            ALTER TABLE items ADD COLUMN embedding vector(768);
        EXCEPTION WHEN undefined_object THEN
            RAISE NOTICE 'vector type not available — embedding column skipped (OK in local dev without Docker)';
        END;
        $$
    """)

    op.create_table(
        "item_comments",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("author", sa.String(255), nullable=False),
        sa.Column("body_md", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False, server_default=sa.text("'comentario'")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "kind IN ('comentario','analisis-ia','decision','cambio-estado')",
            name="item_comments_kind_check",
        ),
    )

    op.create_table(
        "item_events",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "ai_enrichments",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("model", sa.String(60), nullable=False),
        sa.Column("prompt_version", sa.String(20), nullable=False),
        sa.Column("effort", sa.String(5), nullable=True),
        sa.Column("impact", sa.SmallInteger(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=True),
        sa.Column("duplicates", sa.JSON(), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "sentry_issues",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("sentry_issue_id", sa.String(50), nullable=False),
        sa.Column("project", sa.String(60), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("level", sa.String(10), nullable=False, server_default=sa.text("'error'")),
        sa.Column("triage", sa.String(20), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'new'")),
        sa.Column("first_seen", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_seen", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("events_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("item_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sentry_issue_id"),
        sa.CheckConstraint("level IN ('error','warning','info')", name="sentry_issues_level_check"),
        sa.CheckConstraint(
            "triage IS NULL OR triage IN ('pendiente','bug-real','input-malo','3rd-party','ruido')",
            name="sentry_issues_triage_check",
        ),
        sa.CheckConstraint(
            "status IN ('new','linked','resolved','ignored')",
            name="sentry_issues_status_check",
        ),
    )

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("ref_type", sa.String(30), nullable=True),
        sa.Column("ref_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'pendiente'")),
        sa.Column("leased_until", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("log", sa.Text(), nullable=True),
        sa.Column("tokens_total", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "kind IN ('enrich','dedup','triage-sentry','digest-email','fix-externo')",
            name="agent_runs_kind_check",
        ),
        sa.CheckConstraint(
            "status IN ('pendiente','corriendo','ok','error')",
            name="agent_runs_status_check",
        ),
    )


def downgrade() -> None:
    op.drop_table("agent_runs")
    op.drop_table("sentry_issues")
    op.drop_table("ai_enrichments")
    op.drop_table("item_events")
    op.drop_table("item_comments")
    op.execute("ALTER TABLE items DROP COLUMN IF EXISTS embedding")
    op.drop_table("items")
    op.drop_table("scopes")
    op.drop_table("api_tokens")
    op.drop_table("users")
    op.execute("""
        DO $$
        BEGIN
            DROP EXTENSION IF EXISTS vector;
        EXCEPTION WHEN feature_not_supported THEN
            NULL;
        END;
        $$
    """)
