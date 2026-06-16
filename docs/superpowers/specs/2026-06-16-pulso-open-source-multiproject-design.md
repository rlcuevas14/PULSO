# Pulso — Open Source Multiproject Design

**Date:** 2026-06-16  
**Status:** Approved  
**Scope:** Transform `eduk3-pulso` into an independent, self-hostable, open-source backlog tool
for solo-preneurs managing multiple unrelated projects.

---

## Context

Pulso is a **agent-native backlog manager** built with FastAPI + HTMX + Postgres. It was
originally built as the internal backlog tool for Eduk3. This spec defines how it becomes an
independent product anyone can self-host and connect to Claude Code via MCP.

**Target user:** solo-preneur / indie developer managing 2–10 unrelated projects
(e.g., Eduk3, Varajo, DomoProps, Metropol) from a single tool instance.

**Scope constraints:**
- Mono-user (single admin account per instance)
- Multi-project (N independent projects, no cross-project relations)
- Self-hosted (Docker Compose, no external infra required)
- English throughout (code, API, MCP tools, UI, README)

---

## Current State

6 sprints shipped, 93 tests, fully deployed at `pulso.eduk3.cl`. Features:

- 8-state item lifecycle, 9 types, priority matrix (impact × effort)
- Relationship graph (blocks / requires / conflicts / related / part-of)
- MCP-over-HTTP with 19 tools (custom JSON-RPC 2.0, not the SDK)
- AI enrichment (Haiku scoring + Gemini embeddings), degrades without API key
- Threads (feature funnels), Incidents (Sentry), Webhooks (Sentry + GitHub)
- Append-only audit log (`item_events`)
- Single implicit project (everything belongs to the same backlog)

**What's missing:** project isolation, English enums/tools, self-contained Docker setup,
first-run onboarding.

---

## Architecture Decision

**Approach B — Additive with `project_id` on items (chosen)**

Add a `projects` table as the new top-level entity. Propagate `project_id` to all
project-scoped tables including `items` directly (denormalized). Keep `scopes` as optional
sub-groupers within a project (renamed to "areas" in UI/API/MCP to avoid conflict with
MCP auth scopes).

Rejected alternatives:
- A (no `project_id` on items): requires JOIN in every MCP filter query
- C (rename `scopes` → `projects`): high rename friction across codebase

The `project_id` on items is **defensive denormalization**: any write can assert
`item.project_id == token.project_id` locally, without graph traversal. Same pattern as
RLS multi-tenant isolation.

---

## 1. Data Model

### New table: `projects`

```sql
CREATE TABLE projects (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug                  TEXT UNIQUE NOT NULL,   -- "eduk3", "varajo", "domoprops"
    name                  TEXT NOT NULL,
    description           TEXT,
    color                 TEXT,                   -- hex, for visual selector
    repo_url              TEXT,                   -- optional, GitHub repo URL
    github_webhook_secret TEXT,                   -- optional, per-project
    sentry_client_secret  TEXT,                   -- optional, per-project
    sentry_api_token      TEXT,                   -- optional, for resolve API
    sentry_org            TEXT,                   -- optional
    archived_at           TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### New columns on existing tables

| Table | New column | Constraint |
|---|---|---|
| `scopes` | `project_id UUID` | NOT NULL, FK → projects |
| `items` | `project_id UUID` | NOT NULL, FK → projects |
| `api_tokens` | `project_id UUID` | NOT NULL, FK → projects |
| `sentry_issues` | `project_id UUID` | NOT NULL, FK → projects |
| `threads` | `project_id UUID` | NOT NULL, FK → projects |
| `agent_runs` | `project_id UUID` | NOT NULL, FK → projects |

### Enum migration: Spanish → English

All enum values are renamed in-place (Alembic `op.execute` with `ALTER TYPE ... RENAME VALUE`
or recreate + update).

| Column | Old values | New values |
|---|---|---|
| `items.status` | `en-curso, bloqueado, en-revision, hecho, descartado` | `in-progress, blocked, in-review, done, discarded` |
| `items.type` | `seguridad, producto` | `security, product` |
| `items.origen` | `humano, ia-sesion, agente` | `human, ai-session, agent` |
| `item_comments.kind` | `comentario, analisis-ia, cambio-estado` | `comment, ai-analysis, status-change` |

Unchanged (already language-neutral): `idea, backlog, spec, bug, feature, tech-debt, infra,
docs, ops, decision, p0–p3, XS–XL, digest, sentry`.

### Naming: `scopes` → `areas` (UI/API layer only)

The DB table stays `scopes` to minimize migration risk. All external surfaces rename:
- REST endpoints: `/api/scopes/*` → `/api/areas/*`
- MCP tool: `pulso_scopes` → `pulso_areas`
- UI labels: "Scope" → "Area"
- Python code: rename model display name, keep table name

---

## 2. MCP Project Isolation

### Token-per-project (chosen approach)

Each `api_token` row carries a `project_id`. When Claude Code connects to `/mcp` using
a token, **all tool calls are implicitly scoped to that token's project**. The agent cannot
read or write items from a different project regardless of arguments passed.

```
Claude Code (efrain session)       Claude Code (varajo session)
       │                                    │
   Bearer TOKEN_EDUK3                   Bearer TOKEN_VARAJO
       │                                    │
       └──────────────┬─────────────────────┘
                      ▼
               POST /mcp  (single endpoint)
                      │
               resolve token → project_id
                      │
        all queries: WHERE project_id = token.project_id
```

**Security invariant:** every DB query in `mcp/tools.py` receives `project_id` as a required
argument derived from the token. No tool accepts `project_id` as a user-supplied parameter.

### MCP tool rename (Spanish → English)

| Current | New |
|---|---|
| `pulso_contexto` | `pulso_context` |
| `pulso_buscar` | `pulso_search` |
| `pulso_listar` | `pulso_list` |
| `pulso_scopes` | `pulso_areas` |
| `pulso_crear` | `pulso_create` |
| `pulso_avanzar` | `pulso_advance` |
| `pulso_completar` | `pulso_complete` |
| `pulso_relacionar` | `pulso_relate` |
| `pulso_mover_scope` | `pulso_move_area` |
| `pulso_incidentes` | `pulso_incidents` |
| `pulso_incidente` | `pulso_incident` |
| `pulso_incidente_resolver` | `pulso_incident_resolve` |
| `pulso_hilo_listar` | `pulso_thread_list` |
| `pulso_hilo` | `pulso_thread` |
| `pulso_hilo_crear` | `pulso_thread_create` |
| `pulso_hilo_avanzar` | `pulso_thread_advance` |
| `pulso_hilo_vincular` | `pulso_thread_link` |

MCP prompts: `briefing`, `decision` (unchanged — already English).  
MCP resources: `pulso://scope/{name}` → `pulso://area/{name}`, `pulso://graph/{item_id}` (unchanged).

### Setup UX: "Connect with Claude Code"

In project settings (`/projects/{slug}/settings`), a **"Connect" tab** shows:

1. A button "Generate write token" → creates token, shows secret once.
2. The ready-to-paste snippet:
   ```bash
   claude mcp add --transport http {slug} http://localhost:8000/mcp \
     --header "Authorization: Bearer {TOKEN}"
   ```
3. One-click copy button.
4. Instructions: "Paste this in your terminal. Restart your Claude Code session. Done."
5. List of existing tokens for this project (name, scope, created date, last used) with revoke button.

**Adding a second project** follows the same flow: create project → settings → generate token →
copy snippet → `claude mcp add`. Claude Code supports multiple MCP servers simultaneously,
so `efrain` and `varajo` can both be connected in the same session.

---

## 3. UI / Navigation

### Global project selector

The top navbar includes a **project switcher** — a dropdown showing all non-archived projects
with their color dot and name. Selection is stored in the session (`current_project_id`).

All views (backlog, priority, threads, incidents, admin) automatically filter by the active
project. No URL change required (session-based, sufficient for mono-user tool).

Navbar structure:
```
[Pulso logo]  [▼ Eduk3 ●]   Backlog  Priority  Threads  Incidents  [Admin]
```

### Project management pages

| Route | Description |
|---|---|
| `/projects` | List all projects (cards with color, name, item count, last activity) |
| `/projects/new` | Create project form (name, slug, description, color) |
| `/projects/{slug}/settings` | Edit project + integrations + Connect tab |

### Per-project views (filtered by session project)

All existing routes stay the same (`/backlog`, `/priority`, `/threads`, `/incidents`, `/items/{id}`).
They query `WHERE project_id = session.current_project_id`.

### First-run setup (`/setup`)

Shown only when the `users` table is empty. A 3-step wizard:

**Step 1 — Create your account**
- Email + password fields
- Creates the single admin user

**Step 2 — Create your first project**
- Name (auto-generates slug), optional description, color picker

**Step 3 — Connect Claude Code**
- Automatically generates a write token for the new project
- Shows the `claude mcp add` snippet
- "Copy" button + "I've done this" → goes to `/backlog`

No email required beyond login. No external services required to start.

---

## 4. Per-project Integrations

### GitHub webhook

Configured in project settings. One webhook URL per project:
```
POST /webhooks/github/{project_slug}
```
Verifies `X-Hub-Signature-256` against `project.github_webhook_secret`.  
Behavior: commit message containing `pulso:{UUID}` auto-closes the item (transitions to `done`).

### Sentry webhook (optional)

Configured in project settings. One webhook URL per project:
```
POST /webhooks/sentry/{project_slug}
```
Verifies against `project.sentry_client_secret`. When not configured, the route returns 404.  
Behavior: ingest → `sentry_issues` table (NOT auto-backlog) → AI triage → manual promotion.

### AI (global, optional)

AI keys (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`) are instance-level env vars, not per-project.  
All AI features degrade gracefully if keys are absent — no errors, no broken UI, just
enrichment skipped.

---

## 5. Self-hosting

### `docker-compose.yml` (self-contained)

```yaml
services:
  app:
    image: ghcr.io/{owner}/pulso:latest
    ports:
      - "8000:8000"
    env_file: .env
    depends_on:
      db:
        condition: service_healthy

  db:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: pulso
      POSTGRES_PASSWORD: ${DB_PASSWORD}
      POSTGRES_DB: pulso
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U pulso"]
      interval: 5s
      retries: 5

volumes:
  pgdata:
```

### `.env.example`

```bash
# Required
SECRET_KEY=change-me-generate-with-openssl-rand-hex-32
DB_PASSWORD=change-me

# Optional — AI enrichment (both degrade gracefully if absent)
ANTHROPIC_API_KEY=
GEMINI_API_KEY=

# Optional — set if running behind a reverse proxy
BASE_URL=http://localhost:8000
```

### Setup in 3 commands

```bash
git clone https://github.com/{owner}/pulso && cd pulso
cp .env.example .env  # edit SECRET_KEY and DB_PASSWORD
docker compose up -d
# Open http://localhost:8000 → /setup wizard
```

### Alembic runs on startup

The app lifespan runs `alembic upgrade head` before accepting requests. No manual migration
step needed on updates.

---

## 6. Migration Plan (existing `eduk3-pulso` instance)

Migration `v0006` (single Alembic revision, reversible):

```
v0006: multiproject + english enums
  - Create table: projects
  - Alter table scopes: add project_id
  - Alter table items: add project_id
  - Alter table api_tokens: add project_id
  - Alter table sentry_issues: add project_id
  - Alter table threads: add project_id
  - Alter table agent_runs: add project_id
  - INSERT INTO projects (slug, name) VALUES ('eduk3', 'Eduk3')
  - UPDATE all tables SET project_id = (SELECT id FROM projects WHERE slug = 'eduk3')
  - ALTER COLUMN ... USING ... (enum renames)
  - Add NOT NULL constraints after backfill
  - Add FK constraints
```

**Downgrade:** drop `project_id` columns, revert enum values, drop `projects` table.

This migration runs in production on the next deploy tag. The existing Eduk3 data becomes
"Project: Eduk3" — no data loss, no manual steps.

---

## 7. Feature Summary (complete product)

### Projects
- Create / edit / archive projects (slug, name, description, color)
- Global project selector in navbar (session-based)
- Per-project dashboard (item counts by status, recent activity)
- Per-project settings + integrations + Connect tab

### MCP integration
- Single `/mcp` endpoint, token-per-project isolation
- 17 tools (English names), 2 prompts, 2 resource types
- One-click `claude mcp add` snippet per project
- Read tokens (read-only) and write tokens (full access)
- Multiple projects connected simultaneously in one Claude Code session

### Backlog core
- 8-state lifecycle: `idea → backlog → spec → in-progress → blocked → in-review → done → discarded`
- 9 types: `bug, feature, tech-debt, infra, docs, ops, security, product, idea`
- Priority: `p0–p3` (declared) + AI scoring (impact 1–5, effort XS–XL)
- Areas (optional sub-groupers within a project)
- Priority matrix view (impact × effort quadrant)
- Full-text search

### Relationship graph
- Arc types: `blocks / requires / conflicts / related / part-of`
- Derived blocking (computed, not materialized state)
- 2-hop neighborhood via `pulso_context`
- Topological ordering (Kahn's algorithm)

### AI (optional, degrades without keys)
- Impact/effort auto-scoring (LLM)
- Semantic embeddings for similarity search (Gemini)
- Incident triage (noise pre-filter)
- Thread stage elaboration

### Threads
- Feature funnels: `idea → spec → in-progress → done`
- Stage artifacts (notes, decisions, outputs)
- Item ↔ thread linking (`thread_id`)

### Incidents (optional, per project)
- Sentry webhook receiver (per project, HMAC verified)
- Error container (NOT auto-backlog — manual promotion)
- Full stack trace viewer
- Resolve from Pulso (Sentry API)
- Historical backfill

### GitHub integration (optional, per project)
- Webhook receiver (per project, HMAC verified)
- Auto-close item by commit (`pulso:{UUID}` in message)

### Audit log
- `item_events` append-only — every mutation logged (actor, action, payload)
- Visible in item detail view

### Self-hosting
- `docker compose up -d` — app + postgres/pgvector, no other dependencies
- `.env.example` with documented variables
- First-run wizard at `/setup` (account + project + MCP token)
- Alembic auto-runs on startup
- README: what it is, 3-command setup, how to connect Claude Code

---

## Out of Scope (this version)

- Multi-user / team support
- Cloud-hosted version
- Mobile app
- Billing / subscriptions
- Cross-project relations or rollups
- Real-time collaboration
- Plugin system

---

## Open Questions

None — all decisions made during design session (2026-06-16).
