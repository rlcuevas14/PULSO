# Pulso

An agent-native backlog manager for solo-preneurs. Manage all your projects from one database, with Claude Code connected as an MCP client that reads and writes your backlog automatically.

## What it is

- **Backlog + dependency graph + Sentry incidents + development threads** — all accessible via MCP so your Claude Code agent keeps the backlog up to date as it works.
- **Multi-project** — manage N projects in one database. Each MCP token is project-scoped; the agent cannot write to the wrong project.
- **Self-hosted** — runs on any machine with Docker + Postgres.

## Quick start

```bash
git clone https://github.com/your-username/pulso
cd pulso
cp .env.example .env
# Edit .env: set SECRET_KEY and DB_PASSWORD
docker compose up -d
```

Open http://localhost:8000 → you'll be redirected to `/setup` to create your account and first project.

### Connect Claude Code

After setup, go to **Projects → Settings** and generate a write token. Then run:

```bash
claude mcp add --transport http my-project http://localhost:8000/mcp \
  --header "Authorization: Bearer <TOKEN>"
```

Restart Claude Code. The MCP tools will appear prefixed with your project slug.

## MCP tools (17)

| Tool | Description |
|------|-------------|
| `pulso_context` | Session start summary: quick wins, blockers, unlinked Sentry bugs, active threads |
| `pulso_search` | Full-text search across backlog items |
| `pulso_list` | Filtered item list (by status, type, area, order) |
| `pulso_areas` | List areas (backlog groupings) with counts |
| `pulso_create` | Create a backlog item (auto-creates area if needed) |
| `pulso_advance` | Transition item status (validated lifecycle) |
| `pulso_complete` | Mark item done — reports newly unblocked items |
| `pulso_link` | Create a graph edge between items (blocks/requires/conflicts/related/part\_of) |
| `pulso_move_area` | Move item to a different area |
| `pulso_thread_create` | Create a Thread (heavy feature funnel) |
| `pulso_thread_advance` | Advance a Thread to the next stage |
| `pulso_thread_list` | List Threads (filter by stage/area) |
| `pulso_thread` | Thread detail with artifacts and linked items |
| `pulso_thread_link` | Link an existing item to a Thread |
| `pulso_incidents` | List Sentry errors in the incident container |
| `pulso_incident` | Incident detail with stack trace from Sentry |
| `pulso_incident_resolve` | Mark incident resolved in Pulso (and Sentry) |

Every token is project-scoped: tools silently fail-safe if the token has no project assigned.

## Item lifecycle

```
idea → backlog → spec → in-progress → in-review → done
                      ↘ blocked ↗
                      ↘ discarded (from any state)
```

Transitions are validated. Terminal states (`done`/`discarded`) require a reason (via `pulso_complete` or the UI close modal).

## Features

- **Dependency graph** — items link with typed edges (`blocks`, `requires`, etc.). Blocked status is derived (not stored): an item is blocked if it has an open blocker in the graph.
- **Priority matrix** — impact × effort (AI-estimated). Quick wins surfaced automatically.
- **Threads** — funnel for heavy features: idea → investigacion → historias → spec → en-desarrollo → review → hecho.
- **Sentry integration** — errors land in an incident container; triage AI pre-classifies noise; you promote real ones to the backlog manually.
- **GitHub webhook** — `pulso:UUID` in commit messages auto-closes the referenced item.
- **AI enrichment** — impact/effort estimation via Claude Haiku (optional, degrades without `ANTHROPIC_API_KEY`).
- **Semantic search** — embedding-based neighbor lookup via Gemini (optional, requires `GEMINI_API_KEY` + pgvector).

## Configuration

See `.env.example` for all options. Required:

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Session secret (generate with `python -c "import secrets; print(secrets.token_hex(32))"`) |
| `DB_PASSWORD` | Postgres password |

Optional: `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `SENTRY_CLIENT_SECRET`, `SENTRY_API_TOKEN`, `SENTRY_ORG`, `GITHUB_WEBHOOK_SECRET`.

## Running locally (development)

```bash
pip install -r requirements.txt
# Postgres on localhost:5432, database "pulso_test"
TEST_DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/pulso_test" \
  python -m pytest tests/ -q
```

No Node.js required — Tailwind and HTMX load from CDN.

## Deployment

Tag a release to trigger the Docker build + deploy workflow:

```bash
git tag -a v2026.MM.DD-N -m "release notes"
git push origin v2026.MM.DD-N
```

The workflow builds a multi-platform image, pushes to GHCR, SSHs to your server, and runs `alembic upgrade head`.

## Stack

FastAPI · SQLAlchemy async (asyncpg) · Alembic · Jinja2 · HTMX 2 · Tailwind (CDN) · Postgres + pgvector · Custom MCP JSON-RPC 2.0
