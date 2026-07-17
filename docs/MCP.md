# Connecting Claude Code to Pulso (MCP over HTTP)

Pulso exposes an MCP endpoint at `https://<your-pulso-host>/mcp` (Streamable HTTP, JSON mode).
Any Claude Code instance connects with just a token — nothing to install locally.

## 1. Generate a token

Tokens are **project-scoped**: go to `https://<your-pulso-host>/projects/<slug>/settings` →
**Generate MCP token** (scope `write` to create/close items from a session). Copy the token —
it is shown only once.

> Do NOT use `/admin` to mint MCP tokens: tokens created there have no `project_id` and the
> MCP endpoint rejects them.

## 2. Register the server in Claude Code

**Option A — command (writes to `~/.claude.json`, local scope):**
```bash
claude mcp add --transport http my-project https://<your-pulso-host>/mcp \
  --header "Authorization: Bearer <YOUR_TOKEN>"
```

**Option B — `.mcp.json` checked into the repo root (shared with the team):**
```json
{
  "mcpServers": {
    "my-project": {
      "type": "http",
      "url": "https://<your-pulso-host>/mcp",
      "headers": { "Authorization": "Bearer ${PULSO_TOKEN}" }
    }
  }
}
```
Claude Code expands `${PULSO_TOKEN}` from the environment (never commit the token).
Verify with `claude mcp list`. New tools appear only after **restarting** Claude Code.

## 3. Available tools (26)

| Tool | Scope | Purpose |
|------|-------|---------|
| `pulso_context(area?, work_description?)` | read | Session briefing: quick wins, blockers, unlinked incidents, active threads |
| `pulso_search(q, area?, type?, limit?)` | read | Full-text search |
| `pulso_list(area?, status?, type?, order?, quickwins?, limit?)` | read | Filtered list (order: `impact`/`priority`/`topological`/`recent`) |
| `pulso_areas()` | read | List areas (backlog groupings) with counts and examples |
| `pulso_incidents(status?, triage?, limit?)` | read | List Sentry incidents |
| `pulso_incident(issue_id)` | read | Incident detail (with stack trace when available) |
| `pulso_thread_list(stage?)` | read | List development threads |
| `pulso_thread(thread_id)` | read | Thread detail with artifacts and linked items |
| `pulso_create(title, type, area_name, …)` | write | Create item (origin `ai-session`; creates the area if missing) |
| `pulso_advance(item_id\|query, to_status)` | write | Change status (lifecycle-validated; terminals go via `pulso_complete`) |
| `pulso_complete(item_id\|search_query, note?, commit_sha?)` | write | Mark done + report newly unblocked items |
| `pulso_link(source, target, relation, note?)` | write | Create a graph edge (`blocks`/`requires`/`conflicts`/`related`/`part_of`) |
| `pulso_move_area(item_id\|query, area_name)` | write | Move an item to another existing area |
| `pulso_incident_resolve(issue_id, note?)` | write | Resolve a Sentry incident |
| `pulso_thread_create(title, area_name, summary?)` | write | Create a development thread |
| `pulso_thread_advance(thread_id, artifact_content?)` | write | Advance a thread to its next stage |
| `pulso_thread_link(thread_id, item_id\|query)` | write | Link an item to a thread |
| `pulso_doc_list(compartment_id?, status?, q?)` | read | List Management deliverables (metadata only) |
| `pulso_doc_get(deliverable_id, include_content?)` | read | Deliverable detail + version history (inlines content up to 256 KB) |
| `pulso_doc_put(compartment, name, doc_type, content\|content_base64, …)` | write | Create a deliverable or append a version (append-only; auto-creates the compartment) |
| `pulso_pending_list(status?, owner?, overdue?, plan_task_id?)` | read | List project pendings (action items) |
| `pulso_pending_upsert(pending_id?, title?, status?, due_date?, owner?, …)` | write | Create or update a pending (omit `pending_id` to create) |
| `pulso_pending_complete(pending_id)` | write | Mark a pending as done |
| `pulso_gantt_get()` | read | Full project plan: task hierarchy, dates, progress, milestones, deps |
| `pulso_gantt_task_upsert(task_id?, name?, parent_id?, start_date?, end_date?, progress?, …)` | write | Create or update a Gantt task (max 3 levels; the Gantt is edited only via MCP) |
| `pulso_gantt_task_remove(task_id)` | write | Delete a Gantt task (children cascade) |

Prompts: `briefing`, `decision`. Resource templates: `pulso://area/{name}`, `pulso://graph/{item_id}`.

## 4. Breaking change — tool rename (Spanish → English)

Older Pulso versions exposed Spanish tool names. They were renamed once, before the first
public release:

| Old (removed) | Current |
|---------------|---------|
| `pulso_contexto` | `pulso_context` |
| `pulso_buscar` | `pulso_search` |
| `pulso_listar` | `pulso_list` |
| `pulso_crear` | `pulso_create` |
| `pulso_avanzar` | `pulso_advance` |
| `pulso_completar` | `pulso_complete` |
| `pulso_relacionar` | `pulso_link` |

Enum values were also renamed (statuses, types, origins — e.g. `hecho` → `done`,
`ia-sesion` → `ai-session`). If an old client sends Spanish values, calls fail validation —
update the client; there is no compatibility shim.

v0018 completed the rename for threads (same no-shim policy):

| Old (removed) | Current |
|---------------|---------|
| stage `investigacion` | `research` |
| stage `historias` | `stories` |
| stage `en-desarrollo` | `in-development` |
| stage `hecho` | `done` |
| stage `descartado` | `discarded` |
| artifact kind `investigacion` | `research` |
| artifact kind `historias` | `stories` |
| artifact kind `notas` | `notes` |

## 5. Suggested session protocol

- **Session start**: call `pulso_context` to get current priorities, blockers, and open incidents.
- **During work**: `pulso_create` for anything worth tracking; `pulso_advance` as states change.
- **Session end**: `pulso_complete` with `note` + `commit_sha` for everything shipped — the
  commit links the item to code, and the note becomes the close reason shown in the Archive.
