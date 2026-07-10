"""v0016: management (PMO) domain — documentos / plan / pendientes.

New orthogonal domain (not the dev backlog): compartments + deliverables (+ append-only
versions), pendings, plan_tasks (Gantt), and a generic append-only audit table. No backfill
(brand-new domain). project_id is nullable, matching PULSO's code-enforced isolation (v0015).
"""

from alembic import op

revision = "v0016"
down_revision = "v0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE management_events (
            id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id   uuid REFERENCES projects(id) ON DELETE CASCADE,
            entity_type  VARCHAR(20) NOT NULL,
            entity_id    uuid NOT NULL,
            actor        VARCHAR(255) NOT NULL,
            action       VARCHAR(60) NOT NULL,
            payload      jsonb,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX management_events_entity ON management_events(entity_type, entity_id)"
    )
    op.execute("CREATE INDEX management_events_project ON management_events(project_id)")

    op.execute("""
        CREATE TABLE compartments (
            id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id   uuid REFERENCES projects(id) ON DELETE CASCADE,
            name         VARCHAR(120) NOT NULL,
            description  TEXT,
            sort_order   SMALLINT NOT NULL DEFAULT 0,
            created_by   VARCHAR(255),
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT compartments_project_name_uniq UNIQUE (project_id, name)
        )
    """)
    op.execute("CREATE INDEX compartments_project ON compartments(project_id)")

    op.execute("""
        CREATE TABLE deliverables (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id      uuid REFERENCES projects(id) ON DELETE CASCADE,
            compartment_id  uuid NOT NULL REFERENCES compartments(id) ON DELETE CASCADE,
            name            VARCHAR(200) NOT NULL,
            doc_type        VARCHAR(10) NOT NULL
                             CHECK (doc_type IN ('docx','pdf','html','md','xlsx','pptx')),
            status          VARCHAR(15) NOT NULL DEFAULT 'draft'
                             CHECK (status IN ('draft','review','final','archived')),
            owner           VARCHAR(255),
            summary_md      TEXT,
            current_version SMALLINT NOT NULL DEFAULT 1,
            created_by      VARCHAR(255),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT deliverables_compartment_name_uniq UNIQUE (compartment_id, name)
        )
    """)
    op.execute("CREATE INDEX deliverables_project ON deliverables(project_id)")
    op.execute("CREATE INDEX deliverables_compartment ON deliverables(compartment_id)")

    op.execute("""
        CREATE TABLE deliverable_versions (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            deliverable_id  uuid NOT NULL REFERENCES deliverables(id) ON DELETE CASCADE,
            version_no      SMALLINT NOT NULL,
            content         bytea NOT NULL,
            mime            VARCHAR(120) NOT NULL,
            size_bytes      INTEGER NOT NULL,
            sha256          VARCHAR(64) NOT NULL,
            note            TEXT,
            created_by      VARCHAR(255),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT deliverable_versions_no_uniq UNIQUE (deliverable_id, version_no)
        )
    """)

    op.execute("""
        CREATE TABLE plan_tasks (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id    uuid REFERENCES projects(id) ON DELETE CASCADE,
            parent_id     uuid REFERENCES plan_tasks(id) ON DELETE CASCADE,
            name          VARCHAR(200) NOT NULL,
            start_date    DATE,
            end_date      DATE,
            progress      SMALLINT NOT NULL DEFAULT 0
                           CHECK (progress >= 0 AND progress <= 100),
            is_milestone  BOOLEAN NOT NULL DEFAULT false,
            deps          jsonb,
            sort_order    SMALLINT NOT NULL DEFAULT 0,
            created_by    VARCHAR(255),
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX plan_tasks_project ON plan_tasks(project_id)")
    op.execute("CREATE INDEX plan_tasks_parent ON plan_tasks(parent_id)")

    op.execute("""
        CREATE TABLE pendings (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id    uuid REFERENCES projects(id) ON DELETE CASCADE,
            title         VARCHAR(300) NOT NULL,
            detail_md     TEXT,
            owner         VARCHAR(255),
            status        VARCHAR(12) NOT NULL DEFAULT 'open'
                           CHECK (status IN ('open','doing','blocked','done')),
            due_date      DATE,
            plan_task_id  uuid REFERENCES plan_tasks(id) ON DELETE SET NULL,
            created_by    VARCHAR(255),
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            closed_at     TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX pendings_project ON pendings(project_id)")
    op.execute("CREATE INDEX pendings_plan_task ON pendings(plan_task_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS pendings")
    op.execute("DROP TABLE IF EXISTS plan_tasks")
    op.execute("DROP TABLE IF EXISTS deliverable_versions")
    op.execute("DROP TABLE IF EXISTS deliverables")
    op.execute("DROP TABLE IF EXISTS compartments")
    op.execute("DROP TABLE IF EXISTS management_events")
