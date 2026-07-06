# Pulso

**Agent-native backlog manager for solo-preneurs.** Manage multiple projects from one self-hosted instance. Claude Code connects via MCP and keeps your backlog up to date as it works — no manual updates.

---

## How it works

You run Pulso on your own server (or locally). Claude Code connects to it as an MCP server. As the agent codes, it reads context, creates items, advances statuses, and closes completed work — automatically.

Each project gets its own MCP token. The agent cannot write to the wrong project.

---

## Quick start

**Prerequisites:** Docker, Docker Compose, a Postgres instance (included in the compose file).

```bash
git clone https://github.com/rlcuevas14/PULSO
cd PULSO
cp .env.example .env
```

Edit `.env` — at minimum set `SECRET_KEY` and `DB_PASSWORD`:

```bash
SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
DB_PASSWORD=choose-a-password
```

```bash
docker compose up -d
```

Open **http://localhost:8000** → redirects to `/setup` to create your account, first project, and write token in one step.

### Connect Claude Code

After setup, go to **Projects → Settings** → copy the `claude mcp add` command shown there. It looks like:

```bash
claude mcp add --transport http my-project http://localhost:8000/mcp \
  --header "Authorization: Bearer <TOKEN>"
```

Restart Claude Code. Call `pulso_context` at the start of any session to get your current priorities, blockers, and open incidents.

---

## MCP tools (17)

| Tool | What it does |
|------|--------------|
| `pulso_context` | Session briefing: quick wins, blockers, unlinked Sentry errors, active threads |
| `pulso_search` | Full-text search across backlog items |
| `pulso_list` | Filtered item list (status, type, area, order) |
| `pulso_areas` | List areas (groupings) with item counts |
| `pulso_create` | Create a backlog item; auto-creates the area if needed |
| `pulso_advance` | Transition an item's status (lifecycle-validated) |
| `pulso_complete` | Mark an item done — reports newly unblocked items |
| `pulso_link` | Add a graph edge between items (`blocks` / `requires` / `conflicts` / `related` / `part_of`) |
| `pulso_move_area` | Move an item to a different area |
| `pulso_thread_create` | Create a Thread (funnel for heavy features) |
| `pulso_thread_advance` | Advance a Thread to its next stage |
| `pulso_thread_list` | List Threads, filter by stage or area |
| `pulso_thread` | Thread detail with artifacts and linked items |
| `pulso_thread_link` | Link an existing backlog item to a Thread |
| `pulso_incidents` | List Sentry errors in the incident container |
| `pulso_incident` | Incident detail with stack trace fetched from Sentry |
| `pulso_incident_resolve` | Resolve an incident in Pulso (and optionally in Sentry) |

---

## Item lifecycle

```
idea → backlog → spec → in-progress → in-review → done
                              ↕
                           blocked
                              ↓
                          discarded  (available from any state)
```

Transitions are validated — the agent can't make illegal moves. Terminal states (`done` / `discarded`) require a reason. Blocking is **derived**: an item is blocked when it has an open `blocks` arc incoming; no manual flag needed.

---

## Features

- **Dependency graph** — typed edges between items. Blocked status computed in real time, never stale.
- **Priority matrix** — impact × effort, AI-estimated via Claude Haiku. Quick wins surface automatically on the dashboard.
- **Multi-project** — N projects, one database. Token-level isolation: each MCP token is bound to exactly one project.
- **Threads** — a lightweight funnel for features too big to go straight to the backlog (idea → investigation → stories → spec → in-development → review → done).
- **Sentry integration** — errors land in a dedicated incident container. AI triage pre-classifies noise. You (or the agent) promote real issues to the backlog manually.
- **GitHub webhook** — include `pulso:ITEM-UUID` in any commit message to auto-close the referenced item.
- **AI enrichment** — impact/effort estimation via Claude Haiku. Optional; degrades gracefully without `ANTHROPIC_API_KEY`.
- **Semantic search** — embedding-based neighbor lookup via Gemini + pgvector. Optional; requires both `GEMINI_API_KEY` and a Postgres instance with pgvector.
- **No Node.js** — Tailwind and HTMX load from CDN. Server renders HTML; HTMX handles partial updates.
- **Multilingual UI** — English (default), Spanish, and French, switchable from the navbar. Adding a language = one JSON file in `app/i18n/locales/` (CI enforces catalog completeness).
- **Archive** — closed items grouped by ISO week with close reasons and linked commits, plus an on-demand AI weekly summary.

---

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | Yes | Session signing key. Generate: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DB_PASSWORD` | Yes | Postgres password |
| `ANTHROPIC_API_KEY` | No | Enables AI impact/effort estimation and Sentry triage |
| `GEMINI_API_KEY` | No | Enables semantic neighbor search (requires pgvector) |
| `SENTRY_CLIENT_SECRET` | No | HMAC secret for Sentry webhook signature verification |
| `SENTRY_API_TOKEN` | No | Fetches stack traces and resolves issues via Sentry API |
| `SENTRY_ORG` | No | Your Sentry organization slug |
| `GITHUB_WEBHOOK_SECRET` | No | HMAC secret for GitHub webhook (auto-close on commit) |

See `.env.example` for all defaults.

---

## Development

```bash
pip install -r requirements.txt

# Run tests (Postgres required — database "pulso_test")
TEST_DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/pulso_test" \
  python -m pytest tests/ -q

# Lint + type check
ruff check app/ tests/
python -m mypy app/
```

**Dirty DB gotcha:** the test database persists between runs. If you change the schema and see unexpected failures, reset it:
```sql
DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
```
Then re-run pytest — it recreates everything from scratch.

---

## Deployment

Tag a release; the GitHub Actions workflow builds a multi-platform image (amd64 + arm64), pushes to GHCR, SSHs to your server, and runs `alembic upgrade head`:

```bash
git tag -a v2026.MM.DD-1 -m "what changed"
git push origin v2026.MM.DD-1
```

You'll need to configure the workflow secrets (`SSH_HOST`, `SSH_USER`, `SSH_KEY`, `GHCR_TOKEN`) in your repo settings. See `.github/workflows/deploy.yml` for the full spec.

---

## Stack

FastAPI · SQLAlchemy async (asyncpg) · Alembic · Jinja2 · HTMX 2 · Tailwind CSS (CDN) · Postgres + pgvector · MCP JSON-RPC 2.0 (hand-rolled, not the SDK)

---

## License

MIT
