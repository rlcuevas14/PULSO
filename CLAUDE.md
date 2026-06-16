# CLAUDE.md — Pulso

> Agent-native backlog manager for solo-preneurs. Manage N projects from one database;
> Claude Code reads and writes your backlog via MCP, keeping it current as it works.

## What it is

- **Backlog + dependency graph + Sentry incidents + development threads** — all accessible via 17 MCP tools.
- **Multi-project** — one database, N projects. Each MCP token is project-scoped; the agent cannot write to the wrong project.
- **Self-hosted OSS** — Docker + Postgres, no external dependencies except optional AI keys.
- **Repo**: `C:\Proyectos\pulso` (local spin-off of `rlcuevas14/eduk3-pulso`).

---

## Current state

**OSS refactor complete** (Chunks 1–4, commit `2f1bec7`):
- Chunk 1: cloned to `C:\Proyectos\pulso`, stripped Eduk3 references, standalone docker-compose + `.env.example`
- Chunk 2: multi-project DB schema (`projects` table, `project_id` FK on items/scopes/threads/sentry_issues/agent_runs/api_tokens), migrations v0006–v0010
- Chunk 3: English enum rename migration (v0011), all code/templates updated to English values
- Chunk 4: 17 MCP tools renamed to English, project isolation failsafe in MCP dispatch, `/projects` UI, setup wizard, project selector in navbar, public README

---

## Stack

FastAPI + SQLAlchemy async (asyncpg) + Alembic + **Jinja2 + HTMX 2 (CDN) + Tailwind (CDN)** — no Node build. Postgres + pgvector. Asyncio worker in-process (queue in DB, `FOR UPDATE SKIP LOCKED`, no Redis). **MCP is hand-rolled** (JSON-RPC 2.0), NOT the `mcp` SDK — for auth/DB/testability control.

---

## Architecture (`app/`)

| Module | Responsibility |
|--------|----------------|
| `main.py` | `create_app`, lifespan (starts worker), mounts routers + `/mcp` (`mount_mcp`) |
| `config.py` | `Settings` (env vars) |
| `database.py` | engine, `SessionFactory`, `Base`, `get_db` |
| `templates_config.py` | `Jinja2Templates` + globals + `fecha` filter |
| `auth/` | `User`/`ApiToken`, bcrypt + SHA-256 tokens, deps (cookie **or** Bearer), login UI, `/setup` wizard |
| `projects/` | `Project` model, service (CRUD + slug), router (`/projects/*`, `/ui/project/switch`) |
| `items/` | `Item`/`ItemComment`/`ItemEvent`/`AiEnrichment`/`ItemRelationship`; `service.py` (lifecycle-validated mutations); `lifecycle.py` (8-state machine); `graph.py` (neighborhood/blocking/Kahn); `relationships.py` (arcs); `importer.py` (JSONL) |
| `scopes/` | `Scope` (area grouper) + router |
| `threads/` | `Thread`/`ThreadArtifact` (stages), service, router |
| `webhooks/` | `SentryIssue`; service (HMAC verify, ingest, backfill, fetch stack trace, resolve); router |
| `jobs/` | `AgentRun`; `worker.py` (poll-and-lease); `handlers.py` (`enrich`, `triage-sentry`) |
| `ai/` | `llm.py` — isolated/mockable interface to Haiku (enrich/triage/generate_stage) + Gemini (embed). Degrades without API key |
| `mcp/` | `server.py` (JSON-RPC transport + 17 tool registry + auth/scope + project-id failsafe); `tools.py` (implementations) |
| `ui/` | `router.py` — screens (`/`, `/backlog`, `/priority`, `/threads`, `/incidents`, `/items/{id}`, `/admin`) + `/ui/...` HTMX action endpoints |

---

## Data model (real enum values — do NOT invent)

**`items`**: `id, project_id, scope_id, title, summary_md, type, status, priority, effort_ai, impact_ai, impact_rationale, effort_declared, priority_declared, trigger_text, dependencies, origen, source_refs(JSONB), stale_risk, agent_ready, created_by, created_at, updated_at, closed_at, last_touched_at, thread_id` + `embedding vector(768)` (migration-only) + `search_vector` (GENERATED, migration-only).

- **status**: `idea, backlog, spec, in-progress, blocked, in-review, done, discarded`
- **type**: `bug, feature, tech-debt, infra, docs, ops, security, product, idea`
- **priority**: `p0..p3` · **effort_ai**: `XS..XL` · **impact_ai**: `1..5`
- **origen**: `digest, human, ai-session, sentry, agent`
- **item_comments.kind**: `comment, ai-analysis, decision, status-change` (`decision` = decision log)

**Other tables**: `users, api_tokens, projects, scopes, item_comments, item_events, ai_enrichments, sentry_issues, agent_runs, item_relationships, threads, thread_artifacts`.
`item_events(actor, action, payload)` is the **audit primitive** — every mutation must emit one.

**Migrations** (head = `v0011`): v0001 (9 tables) · v0002 (search_vector+GIN) · v0003 (item_relationships) · v0004 (last_touched_at + source_refs→JSONB) · v0005 (threads + items.thread_id) · v0006 (projects table) · v0007 (project_id on scopes) · v0008 (project_id on items+threads+sentry_issues) · v0009 (project_id on agent_runs) · v0010 (project_id on api_tokens) · v0011 (English enum rename).

**Thread stages** (NOT renamed — Spanish still): `idea, investigacion, historias, spec, en-desarrollo, review, hecho, descartado`

---

## MCP — 17 tools

Connect Claude Code to a project (generate token at `/projects/{slug}/settings`):
```bash
claude mcp add --transport http my-project http://localhost:8000/mcp \
  --header "Authorization: Bearer <TOKEN>"
```
`protocolVersion 2025-03-26`. Bearer auth required; write tools require scope `write` (else → `isError`). **New tools only appear after RESTARTING Claude Code** (don't-ask denies unapproved tools — client-side, not a server bug).

Token MUST have `project_id` set (created from `/projects/{slug}/settings`, not `/admin`). MCP dispatch returns `isError` immediately if `token.project_id is None`.

- **Read**: `pulso_context`, `pulso_search`, `pulso_list`, `pulso_areas`, `pulso_incidents`, `pulso_incident`, `pulso_thread_list`, `pulso_thread`
- **Write**: `pulso_create` (accepts `thread_id`), `pulso_advance`, `pulso_complete`, `pulso_link`, `pulso_move_area`, `pulso_incident_resolve`, `pulso_thread_create`, `pulso_thread_advance`, `pulso_thread_link`
- **Prompts**: `briefing`, `decision`. **Resources**: `pulso://area/{name}`, `pulso://graph/{item_id}`.

Items returned include `area` (name) and `thread_id` when set. Graph is item↔item; thread membership is via `thread_id`, not the graph.

---

## Key concepts

- **Item lifecycle** (`lifecycle.py`): 8-state machine with transition matrix, validated in UI/REST/MCP. Terminals (`done`/`discarded`) go through `/close` (require a reason).
- **Live graph**: blocking is **derived** (an item is blocked if it has an open `blocks` arc incoming), not a stored state. `pulso_context` traverses neighborhood in real time (anti context-collapse).
- **Incidents (Sentry)**: errors land in `sentry_issues` (**container**, NOT auto-promoted to backlog). AI triage pre-classifies noise; owner/agent **manually promotes** real ones. HMAC-signed webhook; `pulso:UUID` in commit auto-closes (GitHub webhook).
- **Threads**: funnel for heavy features (80% goes fast through backlog, no thread needed).
- **Append-only / audit**: every mutation emits `ItemEvent`.
- **Project isolation**: every DB query in `mcp/tools.py` filters by `pid = _pid(token)`. Creating items, scopes, or threads sets `project_id = pid`. Cross-project access is impossible via MCP.

---

## Run locally + tests

**No pgvector locally** (degrades gracefully — `embedding` is migration-only column). Postgres on `localhost:5432` (`efrain`/`efrain`), database `pulso_test`:

```bash
TEST_DATABASE_URL="postgresql+asyncpg://efrain:efrain@localhost:5432/pulso_test" \
  python -m pytest tests/ -q
ruff check app/ tests/
python -m mypy app/
```

**Dirty DB gotcha**: `pulso_test` persists between runs; `create_all` does NOT alter existing tables. If you change the schema or see failures that don't happen in CI, **reset**: `DROP SCHEMA public CASCADE; CREATE SCHEMA public;` then re-run.
**`search_vector`** (GENERATED, migration-only) is globally patched in `conftest.py` so full-text works in all tests.
**CI is the real gate** (pgvector/pgvector:pg16). Push to `main` runs CI; deploy is NOT automatic.

---

## Deploy

```bash
git tag -a v2026.MM.DD-N -m "..." && git push origin <tag>
```
Triggers `deploy.yml`: multi-platform build (amd64+arm64) → push to GHCR → SSH to server → `docker compose pull && up -d` → `alembic upgrade head`.

**Required secrets** (`.env` on server): `DB_PASSWORD`, `SECRET_KEY`.
**Optional**: `ANTHROPIC_API_KEY` (AI triage/enrich), `GEMINI_API_KEY` (embeddings), `SENTRY_CLIENT_SECRET`, `SENTRY_API_TOKEN`, `SENTRY_ORG`, `GITHUB_WEBHOOK_SECRET`.

---

## Conventions

- **English throughout**: all enum values, error messages, MCP tool names, and UI copy are English. Thread stages are the only exception (still Spanish — out of scope).
- **Every feature brings tests**; CI green before tagging.
- **LLM always via `app/ai/llm.py`** (isolated and mockable); degrades without API key, never breaks the worker.
- **Trunk-based**: direct commit to `main` allowed; verify locally (ruff+mypy+pytest for the area) before pushing; deploy only by tag.
- External webhooks/writes: verify HMAC signature, emit `ItemEvent`, sanitize untrusted content (XSS).
- `/admin` token creation does NOT set `project_id` — only use `/projects/{slug}/settings` to generate MCP tokens.

---

## How to resume

1. Read this CLAUDE.md.
2. First run: open `http://localhost:8000` → redirects to `/setup` (create account + first project + write token).
3. Local tests against `pulso_test` (reset schema if you suspect dirty DB).
4. Changes → CI green → tag for deploy.
