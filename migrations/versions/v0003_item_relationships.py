"""v0003: grafo — tabla item_relationships (arcos tipados) + índices"""

from alembic import op

revision = "v0003"
down_revision = "v0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE item_relationships (
            source_id  UUID NOT NULL REFERENCES items(id) ON DELETE CASCADE,
            target_id  UUID NOT NULL REFERENCES items(id) ON DELETE CASCADE,
            relation   TEXT NOT NULL,
            note       TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (source_id, target_id, relation),
            CONSTRAINT item_relationships_relation_check
                CHECK (relation IN ('blocks','requires','conflicts','related','part_of')),
            CONSTRAINT item_rel_no_self CHECK (source_id <> target_id)
        )
    """)
    op.execute("CREATE INDEX item_rel_target ON item_relationships(target_id)")
    op.execute("""
        CREATE INDEX item_rel_dep ON item_relationships(target_id, source_id)
        WHERE relation IN ('blocks','requires')
    """)
    # Unicidad simétrica para conflicts/related: (A,B) y (B,A) son el mismo arco.
    op.execute("""
        CREATE UNIQUE INDEX item_rel_sym_uniq
        ON item_relationships (least(source_id,target_id), greatest(source_id,target_id), relation)
        WHERE relation IN ('conflicts','related')
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS item_relationships")
