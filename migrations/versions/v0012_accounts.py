"""v0012: accounts + project_members + account columns; fold existing data into one account."""

import os

from alembic import op

revision = "v0012"
down_revision = "v0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE accounts (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            name        TEXT NOT NULL,
            slug        TEXT UNIQUE NOT NULL,
            is_active   BOOLEAN NOT NULL DEFAULT true,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE project_members (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            project_id  uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            role        TEXT NOT NULL DEFAULT 'editor' CHECK (role IN ('viewer','editor')),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(user_id, project_id)
        )
    """)
    # New columns, nullable first so we can backfill before enforcing NOT NULL.
    op.execute("ALTER TABLE users ADD COLUMN account_id uuid REFERENCES accounts(id) ON DELETE CASCADE")
    op.execute(
        "ALTER TABLE users ADD COLUMN account_role TEXT NOT NULL DEFAULT 'member' "
        "CHECK (account_role IN ('owner','member'))"
    )
    op.execute("ALTER TABLE users ADD COLUMN is_superadmin BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE projects ADD COLUMN account_id uuid REFERENCES accounts(id) ON DELETE CASCADE")

    # Backfill: if any users or projects exist, fold them into one default account.
    default_name = os.getenv("DEFAULT_ACCOUNT_NAME", "Default").replace("'", "''")
    op.execute(f"""
        DO $$
        DECLARE acc uuid; first_user uuid;
        BEGIN
            IF EXISTS (SELECT 1 FROM users) OR EXISTS (SELECT 1 FROM projects) THEN
                INSERT INTO accounts (name, slug) VALUES ('{default_name}', 'default')
                    RETURNING id INTO acc;
                UPDATE projects SET account_id = acc WHERE account_id IS NULL;
                UPDATE users SET account_id = acc WHERE account_id IS NULL;
                -- earliest admin (or earliest user) becomes owner + superadmin
                SELECT id INTO first_user FROM users
                    ORDER BY (role = 'admin') DESC, created_at ASC LIMIT 1;
                UPDATE users SET account_role = 'owner', is_superadmin = true WHERE id = first_user;
            END IF;
        END $$;
    """)

    # Enforce NOT NULL, swap slug uniqueness to per-account, drop the legacy global role.
    op.execute("ALTER TABLE users ALTER COLUMN account_id SET NOT NULL")
    op.execute("ALTER TABLE projects ALTER COLUMN account_id SET NOT NULL")
    op.execute("ALTER TABLE projects DROP CONSTRAINT IF EXISTS projects_slug_key")
    op.execute("CREATE UNIQUE INDEX projects_account_slug_uniq ON projects(account_id, slug)")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS role")
    op.execute("CREATE INDEX project_members_user ON project_members(user_id)")
    op.execute("CREATE INDEX project_members_project ON project_members(project_id)")

    # Area (scope) names are unique per project now, not globally (accounts are isolated:
    # two projects/accounts may each have a "backend" area).
    op.execute("ALTER TABLE scopes DROP CONSTRAINT IF EXISTS scopes_name_key")
    op.execute("CREATE UNIQUE INDEX scopes_project_name_uniq ON scopes(project_id, name)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS scopes_project_name_uniq")
    op.execute("ALTER TABLE scopes ADD CONSTRAINT scopes_name_key UNIQUE (name)")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT")
    op.execute("DROP INDEX IF EXISTS projects_account_slug_uniq")
    op.execute("ALTER TABLE projects ADD CONSTRAINT projects_slug_key UNIQUE (slug)")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS account_id")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS is_superadmin")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS account_role")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS account_id")
    op.execute("DROP TABLE IF EXISTS project_members")
    op.execute("DROP TABLE IF EXISTS accounts")
