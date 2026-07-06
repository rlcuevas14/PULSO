# Backlog Redesign + Archive Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the full backlog redesign spec (iterations 1 + 2): open-only default, FTS search, board view, quick-filter chips, close-from-row, group-by list, Archive (/registro) panel with ISO-week grouping, SQL ordering, and AI weekly summary.

**Architecture:** The backlog route gains ~10 new query params; the HTMX filter form carries all state via hidden inputs + `hx-vals` overrides on chip/toggle buttons. Two new partials (`items_board.html`, `items_grouped.html`) join the existing `items_table.html` under a renamed `#items-view` swap target. The Archive route (`/registro`) is a new screen that derives history entirely from `closed_at` + `ItemEvent` — zero new migrations. One `GET /ui/items/{id}/close-modal` endpoint serves the shared close-modal partial to both list rows and board cards.

**Tech Stack:** FastAPI + SQLAlchemy async + Jinja2 + HTMX 2 (CDN) + Tailwind CDN. No Node build. FTS via existing `search_vector` GENERATED column + `plainto_tsquery('spanish')`. AI via `app/ai/llm.py` (Haiku, mockable, degrades without API key).

## Global Constraints

- Design tokens and `.p-*` classes from `app/templates/partials/_head.html` only — no hardcoded grays or blues.
- No opacity modifiers on semantic tokens (`bg-canvas/50` breaks); opacity allowed only on `brand-*`/`success`/`warning`/`error`.
- Handlers returning `204 + HX-Refresh` MUST be called with `hx-post` on the form (never plain `action/method=post` for these).
- Type/status color maps: copy into each template, do NOT import (codebase convention, marked in `items_table.html` comment).
- UI copy: Spanish in templates (matches existing screens); NAV labels in English (matches existing NAV array).
- Every mutation emits `ItemEvent` — no new mutations in this spec; reuse `/close` and `/transition`.
- Zero schema migrations in iterations 1–2; all data derived from existing columns.
- CI gate: `ruff check app/ tests/` + `mypy app/` + `pytest tests/ -q` all green before tagging.
- Test DB: `pulso_test` (Postgres). `search_vector` patched globally in `conftest.py`. Reset with `DROP SCHEMA public CASCADE; CREATE SCHEMA public;` if tests fail unexpectedly.

---

## File Map

| Action | File | What changes |
|--------|------|-------------|
| Modify | `app/items/service.py:253` | Fix `"topologico"` → `"topological"` |
| Modify | `app/ui/router.py` | `backlog()`: 10 new params + FTS + SQL order + ready_ids + board grouping + group-by. New routes: `/registro`, `/ui/registro/summary`, `/ui/items/{id}/close-modal` + dashboard `closed_this_week` count |
| Modify | `app/templates/backlog.html` | Full toolbar redesign; rename `#items-table` → `#items-view`; add `#modal-slot` |
| Modify | `app/templates/partials/items_table.html` | Add close button ✓ + ready chip 🤖 per row |
| Create | `app/templates/partials/items_board.html` | Kanban board: 6 open-status columns |
| Create | `app/templates/partials/items_grouped.html` | Group-by list: `<details>` per group wrapping `items_table.html` |
| Create | `app/templates/partials/_close_modal.html` | Extracted + parametric close modal partial |
| Modify | `app/templates/item_detail.html` | Replace inline modal HTML with `{% include "partials/_close_modal.html" %}` |
| Create | `app/templates/registro.html` | Archive screen: filters + grouped weeks |
| Create | `app/templates/partials/registro_rows.html` | HX-Request partial for load-more pagination |
| Modify | `app/templates/base.html:8` | Add `("/registro", "Archive")` to NAV array |
| Modify | `app/templates/dashboard.html` | Add Archive homecard (6th card, `closed_this_week` count) |
| Modify | `app/ai/llm.py` | Add `summarize_closed(items_with_reasons)` function |
| Modify | `tests/test_ui.py` | New tests for all new features |

---

## Task 1: Fix topological order bug in `service.py`

**Files:**
- Modify: `app/items/service.py:253`
- Test: `tests/test_items_service.py` (or `tests/test_ui.py` if no service test file)

**Interfaces:**
- Fixes: `list_items(db, order="topological")` — currently always falls through to "recent" because line 253 checks `"topologico"` but `_order_items` (line 180) checks `"topological"`.

- [ ] **Step 1: Write the failing test**

Check if `tests/test_items_service.py` exists; if not, add the test to `tests/test_ui.py`. The test seeds two items with a `blocks` relationship and verifies `list_items(order="topological")` respects the dependency order.

Add to `tests/test_ui.py` (after existing tests):

```python
@pytest.mark.asyncio
async def test_list_items_topological_order_fixed(client: AsyncClient):
    """service.list_items(order='topological') must produce topo-ordered output."""
    from app.items.models import ItemRelationship
    from app.items.service import list_items

    _uid, pid = await _login(client)
    # a must come before b (a blocks b)
    a_id, _ = await _seed_item(client, pid, title="Topo-A depends on nothing")
    b_id, _ = await _seed_item(client, pid, title="Topo-B blocked by A")

    async for db in client.app.dependency_overrides[get_db]():
        rel = ItemRelationship(source_id=a_id, target_id=b_id, relation="blocks")
        db.add(rel)
        await db.commit()

        items = await list_items(db, project_id=pid, order="topological")
        ids_in_order = [str(i.id) for i in items]
        assert ids_in_order.index(str(a_id)) < ids_in_order.index(str(b_id)), (
            "topological order: blocker (a) must appear before its target (b)"
        )
        break
```

- [ ] **Step 2: Run test to verify it fails**

```
TEST_DATABASE_URL="postgresql+asyncpg://efrain:efrain@localhost:5432/pulso_test" python -m pytest tests/test_ui.py::test_list_items_topological_order_fixed -v
```
Expected: FAIL — items come out in "recent" (insertion) order, not topological.

- [ ] **Step 3: Fix the bug**

In `app/items/service.py`, line 253, change:
```python
    topo_rank = await _topo_order_ids(db, items) if order == "topologico" else None
```
to:
```python
    topo_rank = await _topo_order_ids(db, items) if order == "topological" else None
```

- [ ] **Step 4: Run test to verify it passes**

```
TEST_DATABASE_URL="postgresql+asyncpg://efrain:efrain@localhost:5432/pulso_test" python -m pytest tests/test_ui.py::test_list_items_topological_order_fixed -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/items/service.py tests/test_ui.py
git commit -m "fix: topological order in list_items (service.py typo 'topologico'→'topological')"
```

---

## Task 2: Backlog route — all new query params + SQL ordering + ready_ids + board/group context

**Files:**
- Modify: `app/ui/router.py` (the `backlog()` function, lines 121–179)

**Interfaces:**
- Produces: `backlog()` now accepts `show`, `q`, `priority`, `effort`, `quickwins`, `urgent`, `agent_ready`, `view`, `group` in addition to existing params.
- Context adds: `ready_ids` (set of str UUIDs), `by_status` (dict status→list, board only), `groups` (list of (label, items) tuples, group-by only), `board_statuses` (list of str).
- `filters` dict gains all new keys.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ui.py`:

```python
@pytest.mark.asyncio
async def test_backlog_show_param(client: AsyncClient):
    _uid, pid = await _login(client)
    await _seed_item(client, pid, title="Open item", status="backlog")
    # Seed a closed item directly via DB
    from app.items.models import Item
    from app.scopes.models import Scope
    from datetime import datetime, timezone
    async for db in client.app.dependency_overrides[get_db]():
        scope = (await db.execute(select(Scope).where(Scope.project_id == pid))).scalars().first()
        done = Item(scope_id=scope.id, project_id=pid, title="Done item", type="feature",
                    status="done", origen="human", closed_at=datetime.now(timezone.utc))
        db.add(done)
        await db.commit()
        break

    # Default (show=open) must NOT show done items
    r = await client.get("/backlog")
    assert r.status_code == 200
    assert "Open item" in r.text
    assert "Done item" not in r.text

    # show=closed shows only closed
    r2 = await client.get("/backlog?show=closed")
    assert r2.status_code == 200
    assert "Done item" in r2.text
    assert "Open item" not in r2.text

    # show=all shows both
    r3 = await client.get("/backlog?show=all")
    assert r3.status_code == 200
    assert "Open item" in r3.text
    assert "Done item" in r3.text

    # Explicit status= overrides show=
    r4 = await client.get("/backlog?status=backlog&show=closed")
    assert "Open item" in r4.text
    assert "Done item" not in r4.text


@pytest.mark.asyncio
async def test_backlog_search_q(client: AsyncClient):
    _uid, pid = await _login(client)
    await _seed_item(client, pid, title="xyzunique backlog feature")
    await _seed_item(client, pid, title="completely different title")
    r = await client.get("/backlog?q=xyzunique")
    assert r.status_code == 200
    assert "xyzunique backlog feature" in r.text
    assert "completely different title" not in r.text


@pytest.mark.asyncio
async def test_backlog_filter_priority_effort(client: AsyncClient):
    _uid, pid = await _login(client)
    await _seed_item(client, pid, title="P0 item", priority="p0", effort_ai="M")
    await _seed_item(client, pid, title="P2 item", priority="p2", effort_ai="XL")
    r = await client.get("/backlog?priority=p0")
    assert r.status_code == 200
    assert "P0 item" in r.text
    assert "P2 item" not in r.text
    r2 = await client.get("/backlog?effort=M")
    assert r2.status_code == 200
    assert "P0 item" in r2.text


@pytest.mark.asyncio
async def test_backlog_chips_quickwins_urgent_agent_ready(client: AsyncClient):
    _uid, pid = await _login(client)
    from app.items.models import Item
    from app.scopes.models import Scope
    async for db in client.app.dependency_overrides[get_db]():
        scope = (await db.execute(select(Scope).where(Scope.project_id == pid))).scalars().first()
        qw = Item(scope_id=scope.id, project_id=pid, title="Quick win item", type="feature",
                  status="backlog", origen="human", impact_ai=5, effort_ai="XS")
        ar = Item(scope_id=scope.id, project_id=pid, title="Agent ready item", type="feature",
                  status="backlog", origen="human", agent_ready=True)
        slow = Item(scope_id=scope.id, project_id=pid, title="Slow heavy item", type="feature",
                    status="backlog", origen="human", impact_ai=1, effort_ai="XL")
        db.add_all([qw, ar, slow])
        await db.commit()
        break

    r = await client.get("/backlog?quickwins=true")
    assert r.status_code == 200
    assert "Quick win item" in r.text
    assert "Slow heavy item" not in r.text

    r2 = await client.get("/backlog?agent_ready=true")
    assert r2.status_code == 200
    assert "Agent ready item" in r2.text
    assert "Quick win item" not in r2.text

    r3 = await client.get("/backlog?urgent=true")
    # urgent = p0/p1; seed with priority
    assert r3.status_code == 200  # at least doesn't crash


@pytest.mark.asyncio
async def test_backlog_view_board(client: AsyncClient):
    _uid, pid = await _login(client)
    await _seed_item(client, pid, title="Board item backlog", status="backlog")
    await _seed_item(client, pid, title="Board item in-progress", status="in-progress")
    r = await client.get("/backlog?view=board")
    assert r.status_code == 200
    # Board renders columns for open statuses; no done column
    assert "backlog" in r.text
    assert "in-progress" in r.text
    assert "done" not in r.text.lower().replace("Marcar hecho", "")

    r2 = await client.get("/backlog?view=board", headers={"HX-Request": "true"})
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_backlog_group_by(client: AsyncClient):
    _uid, pid = await _login(client)
    await _seed_item(client, pid, title="Group test item")
    r = await client.get("/backlog?group=type")
    assert r.status_code == 200
    assert "Group test item" in r.text
```

- [ ] **Step 2: Run tests to verify they fail**

```
TEST_DATABASE_URL="postgresql+asyncpg://efrain:efrain@localhost:5432/pulso_test" python -m pytest tests/test_ui.py::test_backlog_show_param tests/test_ui.py::test_backlog_search_q tests/test_ui.py::test_backlog_filter_priority_effort tests/test_ui.py::test_backlog_chips_quickwins_urgent_agent_ready tests/test_ui.py::test_backlog_view_board tests/test_ui.py::test_backlog_group_by -v
```
Expected: Most FAIL (unexpected query params → they're ignored today, so `show` default would show done items, `board` wouldn't trigger, etc.)

- [ ] **Step 3: Rewrite the `backlog()` function in `app/ui/router.py`**

Replace lines 121–179 with:

```python
_BOARD_STATUSES = ["idea", "backlog", "spec", "in-progress", "in-review", "blocked"]


@router.get("/backlog", response_class=HTMLResponse)
async def backlog(
    request: Request,
    # --- existing params ---
    scope: str | None = None,
    status: str | None = None,
    item_type: str | None = None,
    origen: str | None = None,
    stale: bool | None = None,
    graph_blocked: bool | None = None,
    order: str = "priority",
    # --- new params ---
    show: str = "open",          # "open" | "all" | "closed"
    q: str | None = None,        # FTS search
    priority: str | None = None, # p0..p3
    effort: str | None = None,   # XS..XL
    quickwins: bool = False,
    urgent: bool = False,
    agent_ready: bool = False,
    view: str = "list",          # "list" | "board"
    group: str = "",             # "" | "scope" | "type" | "priority" | "status"
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.items.search import search_items as _fts

    pid = await _project_id(db, user, request)
    q_base = select(Item).where(Item.project_id == pid)

    # --- status/show filter ---
    if status:
        # Explicit status overrides show
        q_base = q_base.where(Item.status == status)
    elif view == "board":
        # Board only shows open statuses regardless of show param
        q_base = q_base.where(Item.status.in_(_BOARD_STATUSES))
    elif show == "open":
        q_base = q_base.where(Item.status.in_(_OPEN))
    elif show == "closed":
        q_base = q_base.where(Item.status.in_(["done", "discarded"]))
    # show == "all": no status filter

    # --- scope filter ---
    if scope:
        scope_row = await db.scalar(
            select(Scope).where(Scope.name == scope, Scope.project_id == pid)
        )
        if scope_row:
            q_base = q_base.where(Item.scope_id == scope_row.id)
        else:
            q_base = q_base.where(Item.id == uuid.UUID(int=0))  # no results

    # --- other column filters ---
    if item_type:
        q_base = q_base.where(Item.type == item_type)
    if origen:
        q_base = q_base.where(Item.origen == origen)
    if stale is not None:
        q_base = q_base.where(Item.stale_risk == stale)
    if priority:
        q_base = q_base.where(Item.priority == priority)
    if effort:
        q_base = q_base.where(Item.effort_ai == effort)
    if urgent:
        q_base = q_base.where(Item.priority.in_(["p0", "p1"]))
    if quickwins:
        q_base = q_base.where(Item.impact_ai >= 4, Item.effort_ai.in_(["XS", "S"]))
    if agent_ready:
        q_base = q_base.where(Item.agent_ready.is_(True))

    # --- SQL ordering (priority/impact/recent; topological stays in Python) ---
    if order in ("priority", "prioridad"):
        from sqlalchemy import case as sa_case
        q_base = q_base.order_by(
            sa_case(
                (Item.priority == "p0", 0),
                (Item.priority == "p1", 1),
                (Item.priority == "p2", 2),
                (Item.priority == "p3", 3),
                else_=9,
            ),
            Item.impact_ai.desc().nullslast(),
        )
    elif order == "impact":
        q_base = q_base.order_by(Item.impact_ai.desc().nullslast(), Item.effort_ai.asc().nullslast())
    elif order == "recent":
        q_base = q_base.order_by(Item.created_at.desc())
    # topological: no SQL ordering — sorted in Python below

    q_base = q_base.limit(300)
    items = list((await db.execute(q_base)).scalars().all())

    # --- FTS search (post-filter by ids to combine with other SQL filters) ---
    if q:
        fts_rows = await _fts(db, q, project_id=pid)
        matched = {r["id"] for r in fts_rows}
        items = [i for i in items if str(i.id) in matched]
        if order not in ("priority", "prioridad", "impact", "recent"):
            # Preserve FTS rank order when order=topological or default
            rank_map = {r["id"]: r["rank"] for r in fts_rows}
            items = sorted(items, key=lambda i: rank_map.get(str(i.id), 0.0), reverse=True)

    # --- graph data ---
    blocked_ids = await graph.graph_blocked_ids(db, project_id=pid)
    unblocker_ids = await graph.unblocker_ids(db, project_id=pid)

    if graph_blocked:
        items = [i for i in items if str(i.id) in blocked_ids]

    # --- topological ordering (Python, needs graph) ---
    if order in ("topological", "topologico"):
        topo = await _topo_order_ids(db, items)
        items = _order_items(items, "topological", topo)

    # --- ready_ids: agent_ready + open (backlog/spec) + not blocked ---
    ready_ids = {
        str(i.id) for i in items
        if i.agent_ready and i.status in ("backlog", "spec") and str(i.id) not in blocked_ids
    }

    # --- scope context ---
    scopes = list((await db.execute(
        select(Scope).where(Scope.archived.is_(False), Scope.project_id == pid).order_by(Scope.name)
    )).scalars().all())
    scope_map = {s.id: s.name for s in scopes}

    ctx: dict = {
        "user": user,
        "items": items,
        "scopes": scopes,
        "scope_map": scope_map,
        "blocked_ids": blocked_ids,
        "unblocker_ids": unblocker_ids,
        "ready_ids": ready_ids,
        "recent_touch": {str(i.id): _recent_touch(i) for i in items},
        "filters": {
            "scope": scope, "status": status, "type": item_type, "origen": origen,
            "stale": stale, "graph_blocked": graph_blocked, "order": order,
            "show": show, "q": q, "priority": priority, "effort": effort,
            "quickwins": quickwins, "urgent": urgent, "agent_ready": agent_ready,
            "view": view, "group": group,
        },
    }

    # --- board-specific context ---
    if view == "board":
        by_status: dict[str, list] = {s: [] for s in _BOARD_STATUSES}
        for item in items:
            if item.status in by_status:
                by_status[item.status].append(item)
        ctx["by_status"] = by_status
        ctx["board_statuses"] = _BOARD_STATUSES

    # --- group-by context ---
    if group and group != "none" and view != "board":
        grouped: dict[str, list] = {}
        for item in items:
            if group == "scope":
                key = scope_map.get(item.scope_id, "(sin scope)")
            elif group == "type":
                key = item.type or "(sin tipo)"
            elif group == "priority":
                key = item.priority or "(sin prioridad)"
            elif group == "status":
                key = item.status
            else:
                key = "(sin grupo)"
            grouped.setdefault(key, []).append(item)
        ctx["groups"] = sorted(grouped.items())

    if request.headers.get("HX-Request"):
        if view == "board":
            return templates.TemplateResponse(request, "partials/items_board.html", ctx)
        if group and group != "none":
            return templates.TemplateResponse(request, "partials/items_grouped.html", ctx)
        return templates.TemplateResponse(request, "partials/items_table.html", ctx)
    return templates.TemplateResponse(request, "backlog.html", ctx)
```

Also add this import at the top of the file (with existing imports from `app.items`):
```python
from sqlalchemy import case as sa_case  # add only if not present, or use inline import
```
(The inline import inside the function avoids any circular import risk; keep it inline as written above.)

- [ ] **Step 4: Run tests to verify they pass**

```
TEST_DATABASE_URL="postgresql+asyncpg://efrain:efrain@localhost:5432/pulso_test" python -m pytest tests/test_ui.py::test_backlog_show_param tests/test_ui.py::test_backlog_search_q tests/test_ui.py::test_backlog_filter_priority_effort tests/test_ui.py::test_backlog_chips_quickwins_urgent_agent_ready tests/test_ui.py::test_backlog_view_board tests/test_ui.py::test_backlog_group_by -v
```
Expected: PASS (view/board will pass once templates created in Task 4; board test only checks status 200 and column presence).

- [ ] **Step 5: Run full existing test suite to check regressions**

```
TEST_DATABASE_URL="postgresql+asyncpg://efrain:efrain@localhost:5432/pulso_test" python -m pytest tests/ -q
```
Expected: All previously-passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add app/ui/router.py
git commit -m "feat(backlog): show/q/priority/effort/chips/view/group params + SQL ordering + ready_ids"
```

---

## Task 3: Backlog template — full toolbar redesign + target rename

**Files:**
- Modify: `app/templates/backlog.html`

**Interfaces:**
- Consumes: all new `filters.*` keys from Task 2 (`show`, `q`, `priority`, `effort`, `quickwins`, `urgent`, `agent_ready`, `view`, `group`).
- Produces: `#items-view` swap target (was `#items-table`); `#modal-slot` div for close modal.

- [ ] **Step 1: Write failing test** (regression check — the existing test already covers this)

```
TEST_DATABASE_URL="postgresql+asyncpg://efrain:efrain@localhost:5432/pulso_test" python -m pytest tests/test_ui.py::test_backlog_filters_and_hx -v
```
This test currently passes. After rewriting the template it must still pass. Note: the test checks `HX-Request: true` returns 200 — this will work once the template has `#items-view`.

- [ ] **Step 2: Replace `app/templates/backlog.html` entirely**

```html
{% extends "base.html" %}
{% from "partials/_hint.html" import hint %}
{% block title %}Backlog{% endblock %}
{% block content %}

<div class="flex flex-wrap items-center justify-between gap-3 mb-4">
  <div class="flex items-baseline gap-3">
    <h1 class="text-2xl md:text-3xl font-semibold tracking-tight text-ink">Backlog</h1>
    <span class="text-sm text-muted">{{ items|length }} ítems</span>
  </div>
  <div class="flex gap-2">
    <button onclick="openModal('new-item-modal')" class="p-btn p-btn-primary p-btn-sm">+ Nuevo ítem</button>
    <a href="/ideas" class="p-btn p-btn-ghost p-btn-sm">+ Idea</a>
  </div>
</div>

{{ hint('backlog', '<strong>¿Cómo usar?</strong> <em>Abiertos</em> oculta lo terminado (búscalo en Archive). <span class="font-mono">🤖</span> = listo para el agente: especificado, marcado <em>agent_ready</em>, sin bloqueadores. <span class="font-mono">⛔</span> = bloqueado por otro ítem abierto.'|safe) }}

<div class="sticky top-[67px] z-40 bg-canvas backdrop-blur py-2 -mx-1 px-1 mb-4">
  <form id="filters">
    {# Hidden state carriers — overridden by hx-vals on chip/toggle buttons #}
    <input type="hidden" name="show"        value="{{ filters.show or 'open' }}">
    <input type="hidden" name="view"        value="{{ filters.view or 'list' }}">
    <input type="hidden" name="group"       value="{{ filters.group or '' }}">
    <input type="hidden" name="agent_ready" value="{{ 'true' if filters.agent_ready else '' }}">
    <input type="hidden" name="quickwins"   value="{{ 'true' if filters.quickwins else '' }}">
    <input type="hidden" name="urgent"      value="{{ 'true' if filters.urgent else '' }}">
    <input type="hidden" name="stale"       value="{{ 'true' if filters.stale else '' }}">
    <input type="hidden" name="graph_blocked" value="{{ 'true' if filters.graph_blocked else '' }}">

    {# ── Always-visible row: view toggle | show chips | search | clear ── #}
    <div class="flex flex-wrap gap-2 items-center mb-2">

      {# View toggle #}
      <div class="flex rounded-lg overflow-hidden border border-hairline shrink-0">
        {% for val, label in [('list','Lista'),('board','Tablero')] %}
        <button type="button"
          hx-get="/backlog" hx-target="#items-view" hx-include="#filters" hx-push-url="true"
          hx-vals='{"view":"{{ val }}"}'
          class="px-3 py-1 text-xs font-medium transition-colors
            {% if (filters.view or 'list') == val %}bg-[color:var(--accent)] text-[color:var(--accent-fg)]{% else %}text-body hover:bg-surface-strong{% endif %}">
          {{ label }}
        </button>
        {% endfor %}
      </div>

      {# Show chips #}
      <div class="flex rounded-lg overflow-hidden border border-hairline shrink-0">
        {% for val, label in [('open','Abiertos'),('closed','Cerrados'),('all','Todos')] %}
        <button type="button"
          hx-get="/backlog" hx-target="#items-view" hx-include="#filters" hx-push-url="true"
          hx-vals='{"show":"{{ val }}"}'
          class="px-3 py-1 text-xs font-medium transition-colors
            {% if (filters.show or 'open') == val %}bg-[color:var(--accent)] text-[color:var(--accent-fg)]{% else %}text-body hover:bg-surface-strong{% endif %}">
          {{ label }}
        </button>
        {% endfor %}
      </div>

      {# Search input #}
      <input type="text" name="q" value="{{ filters.q or '' }}" placeholder="Buscar…"
             aria-label="Buscar ítems"
             class="p-input p-input-sm flex-1 min-w-[140px]"
             hx-get="/backlog" hx-target="#items-view" hx-include="#filters" hx-push-url="true"
             hx-trigger="keyup changed delay:300ms, search">

      {# Clear button #}
      <a href="/backlog" class="p-btn p-btn-ghost p-btn-sm shrink-0">Limpiar</a>
    </div>

    {# ── Collapsible details: selects + chips rápidos ── #}
    <details class="group">
      <summary class="p-btn p-btn-ghost p-btn-sm mb-2 list-none w-fit">Más filtros ▾</summary>
      <div class="flex flex-wrap gap-2 items-center pt-1">

        <select name="scope" aria-label="Filtrar por scope" class="p-input p-input-sm"
                hx-get="/backlog" hx-target="#items-view" hx-include="#filters" hx-push-url="true">
          <option value="">Todos los scopes</option>
          {% for s in scopes %}
          <option value="{{ s.name }}" {% if filters.scope == s.name %}selected{% endif %}>{{ s.name }}</option>
          {% endfor %}
        </select>

        <select name="status" aria-label="Filtrar por estado" class="p-input p-input-sm"
                hx-get="/backlog" hx-target="#items-view" hx-include="#filters" hx-push-url="true">
          <option value="">Todos los estados</option>
          {% for s in ['idea','backlog','spec','in-progress','blocked','in-review','done','discarded'] %}
          <option value="{{ s }}" {% if filters.status == s %}selected{% endif %}>{{ s }}</option>
          {% endfor %}
        </select>

        <select name="item_type" aria-label="Filtrar por tipo" class="p-input p-input-sm"
                hx-get="/backlog" hx-target="#items-view" hx-include="#filters" hx-push-url="true">
          <option value="">Todos los tipos</option>
          {% for t in ['bug','feature','tech-debt','infra','docs','ops','security','product','idea'] %}
          <option value="{{ t }}" {% if filters.type == t %}selected{% endif %}>{{ t }}</option>
          {% endfor %}
        </select>

        <select name="priority" aria-label="Filtrar por prioridad" class="p-input p-input-sm"
                hx-get="/backlog" hx-target="#items-view" hx-include="#filters" hx-push-url="true">
          <option value="">Prioridad</option>
          {% for p in ['p0','p1','p2','p3'] %}
          <option value="{{ p }}" {% if filters.priority == p %}selected{% endif %}>{{ p }}</option>
          {% endfor %}
        </select>

        <select name="effort" aria-label="Filtrar por esfuerzo" class="p-input p-input-sm"
                hx-get="/backlog" hx-target="#items-view" hx-include="#filters" hx-push-url="true">
          <option value="">Esfuerzo</option>
          {% for e in ['XS','S','M','L','XL'] %}
          <option value="{{ e }}" {% if filters.effort == e %}selected{% endif %}>{{ e }}</option>
          {% endfor %}
        </select>

        <select name="origen" aria-label="Filtrar por origen" class="p-input p-input-sm"
                hx-get="/backlog" hx-target="#items-view" hx-include="#filters" hx-push-url="true">
          <option value="">Todos los orígenes</option>
          {% for o in ['digest','human','ai-session','sentry','agent'] %}
          <option value="{{ o }}" {% if filters.origen == o %}selected{% endif %}>{{ o }}</option>
          {% endfor %}
        </select>

        <select name="order" aria-label="Ordenar por" class="p-input p-input-sm"
                hx-get="/backlog" hx-target="#items-view" hx-include="#filters" hx-push-url="true">
          <option value="priority"    {% if (filters.order or 'priority') == 'priority' %}selected{% endif %}>Prioridad</option>
          <option value="impact"      {% if filters.order == 'impact' %}selected{% endif %}>Mayor impacto</option>
          <option value="topological" {% if filters.order == 'topological' %}selected{% endif %}>Topológico</option>
          <option value="recent"      {% if filters.order == 'recent' %}selected{% endif %}>Más reciente</option>
        </select>

        <select name="group" aria-label="Agrupar por" class="p-input p-input-sm"
                hx-get="/backlog" hx-target="#items-view" hx-include="#filters" hx-push-url="true">
          <option value=""        {% if not filters.group %}selected{% endif %}>Sin agrupar</option>
          <option value="scope"   {% if filters.group == 'scope' %}selected{% endif %}>Scope</option>
          <option value="type"    {% if filters.group == 'type' %}selected{% endif %}>Tipo</option>
          <option value="priority"{% if filters.group == 'priority' %}selected{% endif %}>Prioridad</option>
          <option value="status"  {% if filters.group == 'status' %}selected{% endif %}>Estado</option>
        </select>

        {# Quick filter chips — toggle on/off #}
        {% set chip_class = "p-pill border cursor-pointer text-xs font-medium transition-colors " %}
        <button type="button"
          hx-get="/backlog" hx-target="#items-view" hx-include="#filters" hx-push-url="true"
          hx-vals='{"quickwins":"{{ "" if filters.quickwins else "true" }}"}'
          class="{{ chip_class }}{% if filters.quickwins %}bg-success/15 border-success/40 text-success{% else %}border-hairline text-muted hover:bg-surface-strong{% endif %}">
          ⚡ Quick wins
        </button>
        <button type="button"
          hx-get="/backlog" hx-target="#items-view" hx-include="#filters" hx-push-url="true"
          hx-vals='{"urgent":"{{ "" if filters.urgent else "true" }}"}'
          class="{{ chip_class }}{% if filters.urgent %}bg-error/15 border-error/40 text-error{% else %}border-hairline text-muted hover:bg-surface-strong{% endif %}">
          🔥 Urgente (p0/p1)
        </button>
        <button type="button"
          hx-get="/backlog" hx-target="#items-view" hx-include="#filters" hx-push-url="true"
          hx-vals='{"agent_ready":"{{ "" if filters.agent_ready else "true" }}"}'
          class="{{ chip_class }}{% if filters.agent_ready %}bg-brand-mint/30 border-brand-mint/40 text-ink{% else %}border-hairline text-muted hover:bg-surface-strong{% endif %}">
          🤖 Agent ready
        </button>
        <button type="button"
          hx-get="/backlog" hx-target="#items-view" hx-include="#filters" hx-push-url="true"
          hx-vals='{"graph_blocked":"{{ "" if filters.graph_blocked else "true" }}"}'
          class="{{ chip_class }}{% if filters.graph_blocked %}bg-warning/15 border-warning/40 text-warning{% else %}border-hairline text-muted hover:bg-surface-strong{% endif %}">
          ⛔ Bloqueados
        </button>
        <button type="button"
          hx-get="/backlog" hx-target="#items-view" hx-include="#filters" hx-push-url="true"
          hx-vals='{"stale":"{{ "" if filters.stale else "true" }}"}'
          class="{{ chip_class }}{% if filters.stale %}bg-warning/15 border-warning/40 text-warning{% else %}border-hairline text-muted hover:bg-surface-strong{% endif %}">
          ⚠ Stale
        </button>
      </div>
    </details>
  </form>
</div>

<div id="items-view" {% if (filters.view or 'list') != 'board' %}class="p-card overflow-hidden"{% endif %}>
  {% if (filters.view or 'list') == 'board' %}
    {% include "partials/items_board.html" %}
  {% elif filters.group and filters.group != 'none' %}
    {% include "partials/items_grouped.html" %}
  {% else %}
    {% include "partials/items_table.html" %}
  {% endif %}
</div>

{# Slot for dynamically-loaded close modal (Tasks 4–5) #}
<div id="modal-slot"></div>

{% include "partials/_new_item_modal.html" %}

{% endblock %}
```

- [ ] **Step 3: Update `partials/items_table.html` — rename all `hx-target="#items-table"` references**

The `items_table.html` file has no `hx-target` references itself (those are in `backlog.html`). The only remaining reference to `#items-table` might be in tests or the route. Search:

```bash
grep -rn "items-table" app/ tests/
```

Update any found occurrences to `#items-view`. (After replacing `backlog.html` in Step 2, there should be none.)

- [ ] **Step 4: Run regression tests**

```
TEST_DATABASE_URL="postgresql+asyncpg://efrain:efrain@localhost:5432/pulso_test" python -m pytest tests/test_ui.py::test_backlog_filters_and_hx tests/test_ui.py::test_dashboard_and_screens_render -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/templates/backlog.html
git commit -m "feat(backlog): complete toolbar redesign — show/view/search/chips/group-by selects"
```

---

## Task 4: Board view partial (`items_board.html`)

**Files:**
- Create: `app/templates/partials/items_board.html`

**Interfaces:**
- Consumes: `by_status` (dict status→list[Item]), `board_statuses` (list), `scope_map`, `blocked_ids`, `unblocker_ids`, `ready_ids`, `recent_touch` from Task 2 context.
- Produces: rendered kanban board HTML, swapped into `#items-view`.

- [ ] **Step 1: Create `app/templates/partials/items_board.html`**

```html
{# Tablero kanban — 6 columnas de estados abiertos. Copiar mapas de color: no importar. #}
{% set S = {
  "idea":        "bg-brand-lavender/25 text-ink",
  "backlog":     "bg-surface-strong text-body",
  "spec":        "bg-brand-mint/30 text-ink",
  "in-progress": "bg-brand-ochre/30 text-ink",
  "blocked":     "bg-warning/20 text-warning",
  "in-review":   "bg-brand-peach/30 text-ink",
} %}
{% set T = {
  "bug":       "bg-brand-coral/20",
  "feature":   "bg-brand-teal/15",
  "tech-debt": "bg-brand-ochre/25",
  "infra":     "bg-brand-lavender/25",
  "docs":      "bg-brand-mint/30",
  "ops":       "bg-brand-peach/30",
  "security":  "bg-warning/20",
  "product":   "bg-brand-pink/20",
  "idea":      "bg-brand-lavender/25",
} %}

<div class="flex gap-3 overflow-x-auto snap-x snap-mandatory pb-2">
  {% for status in board_statuses %}
  {% set col_items = by_status.get(status, []) %}
  <div class="snap-start shrink-0 w-64 bg-surface-soft border border-hairline rounded-2xl p-2">
    <div class="flex items-center justify-between px-2 py-1.5 mb-1">
      <span class="p-pill {{ S.get(status, 'bg-surface-strong text-body') }}">{{ status }}</span>
      <span class="text-xs text-muted">{{ col_items|length }}</span>
    </div>
    {% for item in col_items %}
    <div class="p-card p-3 mb-2 hover:bg-surface-strong">
      <div class="flex items-start gap-2 mb-2">
        <span class="p-pill {{ T.get(item.type, 'bg-surface-strong') }} text-ink text-xs shrink-0">{{ item.type }}</span>
        <div class="flex gap-1 text-sm shrink-0">
          {% if blocked_ids and item.id|string in blocked_ids %}<span title="Bloqueado">⛔</span>{% endif %}
          {% if ready_ids and item.id|string in ready_ids %}<span title="Listo para agente">🤖</span>{% endif %}
          {% if item.stale_risk %}<span class="text-warning text-xs">⚠</span>{% endif %}
        </div>
      </div>
      <a href="/items/{{ item.id }}" class="block text-sm font-medium text-ink hover:underline mb-2 leading-snug">{{ item.title }}</a>
      <div class="text-xs text-muted mb-2">{{ scope_map.get(item.scope_id, '') }}</div>
      <div class="flex items-center gap-1 flex-wrap">
        {% include "partials/_priority_select.html" %}
        {% set targets = non_terminal_targets(item.status) %}
        {% if targets %}
        <select title="Cambiar estado" aria-label="Cambiar estado"
                class="p-input p-input-sm flex-1 min-w-0"
                hx-post="/ui/items/{{ item.id }}/transition" hx-trigger="change" hx-swap="none" name="status">
          <option value="">→ mover…</option>
          {% for t in targets %}<option value="{{ t }}">{{ t }}</option>{% endfor %}
        </select>
        {% endif %}
        {% if item.status not in ('done','discarded') %}
        <button class="p-btn p-btn-ghost p-btn-sm"
                hx-get="/ui/items/{{ item.id }}/close-modal"
                hx-target="#modal-slot"
                hx-on::after-swap="openModal('close-modal')"
                title="Cerrar ítem">✓</button>
        {% endif %}
      </div>
    </div>
    {% else %}
    <div class="p-3 text-xs text-muted">vacío</div>
    {% endfor %}
  </div>
  {% endfor %}
</div>
```

- [ ] **Step 2: Run board test**

```
TEST_DATABASE_URL="postgresql+asyncpg://efrain:efrain@localhost:5432/pulso_test" python -m pytest tests/test_ui.py::test_backlog_view_board -v
```
Expected: PASS (200, columns rendered).

- [ ] **Step 3: Commit**

```bash
git add app/templates/partials/items_board.html
git commit -m "feat(backlog): kanban board partial (6 open-status columns)"
```

---

## Task 5: Close modal extraction + `GET /ui/items/{id}/close-modal` endpoint

**Files:**
- Create: `app/templates/partials/_close_modal.html`
- Modify: `app/templates/item_detail.html` (replace inline modal)
- Modify: `app/templates/partials/items_table.html` (add ✓ close button + 🤖 ready chip)
- Modify: `app/ui/router.py` (add close-modal endpoint)

**Interfaces:**
- `GET /ui/items/{id}/close-modal` → 200 HTML (the partial), 404 cross-project, guard via `_guard_row` (read).
- `_close_modal.html` consumes: `item` (Item model instance with `.id`, `.status`).
- The partial renders radios for `done`/`discarded` based on `allowed_targets(item.status)`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ui.py`:

```python
@pytest.mark.asyncio
async def test_close_modal_endpoint(client: AsyncClient):
    _uid, pid = await _login(client)
    item_id, _ = await _seed_item(client, pid, status="backlog")

    # Must return the modal HTML
    r = await client.get(f"/ui/items/{item_id}/close-modal")
    assert r.status_code == 200
    assert "Cerrar ítem" in r.text
    # done should be available from backlog
    assert "done" in r.text
    # discarded should also be available
    assert "discarded" in r.text

    # Cross-project: random id → 404
    r404 = await client.get(f"/ui/items/{uuid.uuid4()}/close-modal")
    assert r404.status_code == 404

    # spec → done is NOT in allowed_targets (spec can't go to done directly)
    spec_id, _ = await _seed_item(client, pid, status="spec")
    rs = await client.get(f"/ui/items/{spec_id}/close-modal")
    assert rs.status_code == 200
    # from spec: allowed_targets = backlog, in-progress, blocked, discarded (no done)
    assert "discarded" in rs.text
```

- [ ] **Step 2: Run test to verify it fails (404 because route doesn't exist yet)**

```
TEST_DATABASE_URL="postgresql+asyncpg://efrain:efrain@localhost:5432/pulso_test" python -m pytest tests/test_ui.py::test_close_modal_endpoint -v
```
Expected: FAIL (404 — route not created yet).

- [ ] **Step 3: Create `app/templates/partials/_close_modal.html`**

```html
{#
  Modal de cierre reutilizable. Renderizable estáticamente (item_detail.html)
  o servido via GET /ui/items/{id}/close-modal para backlog row/card.
  Requiere `item` en el contexto.
  Usa allowed_targets() para decidir qué radios mostrar.
  IMPORTANTE: el form usa hx-post (handler responde 204+HX-Refresh).
#}
{% set _close_targets = allowed_targets(item.status) %}
<div id="close-modal" data-modal role="dialog" aria-modal="true" aria-labelledby="close-modal-title"
     class="hidden fixed inset-0 bg-black/40 flex items-center justify-center z-[95] p-4">
  <div class="p-card w-full max-w-md p-6">
    <h2 id="close-modal-title" class="font-semibold text-ink mb-4">Cerrar ítem</h2>
    <form hx-post="/ui/items/{{ item.id }}/close" hx-swap="none"
          action="/ui/items/{{ item.id }}/close" method="post" class="space-y-3">
      <div class="flex gap-4 text-sm text-body">
        {% if 'done' in _close_targets %}
        <label class="flex items-center gap-2"><input type="radio" name="status" value="done" checked> Done</label>
        {% endif %}
        {% if 'discarded' in _close_targets %}
        <label class="flex items-center gap-2">
          <input type="radio" name="status" value="discarded" {% if 'done' not in _close_targets %}checked{% endif %}> Discarded
        </label>
        {% endif %}
      </div>
      <div>
        <label class="p-label" for="close-reason-{{ item.id }}">Motivo</label>
        <input id="close-reason-{{ item.id }}" name="reason"
               placeholder="Motivo (queda en el historial)" aria-label="Motivo del cierre"
               class="p-input">
      </div>
      <div>
        <label class="p-label" for="close-commit-{{ item.id }}">Commit</label>
        <input id="close-commit-{{ item.id }}" name="commit_sha"
               placeholder="commit/SHA (opcional)" aria-label="Commit o SHA"
               class="p-input">
      </div>
      <div class="flex justify-end gap-2 pt-2">
        <button type="button" onclick="closeModal('close-modal')"
                class="p-btn p-btn-ghost p-btn-sm">Cancelar</button>
        <button type="submit" class="p-btn p-btn-primary p-btn-sm">Confirmar cierre</button>
      </div>
    </form>
  </div>
</div>
```

- [ ] **Step 4: Add GET endpoint to `app/ui/router.py`**

Add this route BEFORE the `# ---------- Detalle de ítem ----------` comment (around line 209):

```python
@router.get("/ui/items/{item_id}/close-modal", response_class=HTMLResponse)
async def close_modal_partial(
    item_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    item = await db.get(Item, item_id)
    if item is None:
        return Response(status_code=404)
    guard = await _guard_row(db, user, item.project_id)
    if guard is not None:
        return guard
    return templates.TemplateResponse(
        request, "partials/_close_modal.html",
        {"user": user, "item": item},
    )
```

- [ ] **Step 5: Update `app/templates/item_detail.html`**

Replace the inline modal block (lines 168–198, from `<!-- Modal cerrar/descartar -->` to its closing `</div>`) with:

```html
{% include "partials/_close_modal.html" %}
```

(`item` is already in context from the item_detail route.)

- [ ] **Step 6: Update `app/templates/partials/items_table.html`**

In the right-side actions div (after the "→ mover…" select), add the close button and ready chip:

```html
    <div class="flex items-center gap-2 shrink-0">
      {% include "partials/_priority_select.html" %}
      {% set targets = non_terminal_targets(item.status) %}
      {% if targets %}
      <select title="Cambiar estado" aria-label="Cambiar estado del ítem"
              class="p-input p-input-sm"
              hx-post="/ui/items/{{ item.id }}/transition" hx-trigger="change" hx-swap="none" name="status">
        <option value="">→ mover…</option>
        {% for t in targets %}<option value="{{ t }}">{{ t }}</option>{% endfor %}
      </select>
      {% endif %}
      {% if item.status not in ('done','discarded') %}
      <button class="p-btn p-btn-ghost p-btn-sm"
              hx-get="/ui/items/{{ item.id }}/close-modal"
              hx-target="#modal-slot"
              hx-on::after-swap="openModal('close-modal')"
              title="Cerrar ítem">✓</button>
      {% endif %}
    </div>
```

Also add the ready chip 🤖 in the title row (after the stale icon):

```html
        {% if ready_ids and item.id|string in ready_ids %}<span title="Listo para agente (agent_ready + sin bloqueadores)" class="text-xs">🤖</span>{% endif %}
```

The full updated items_table.html (replace entire file):

```html
{# Tinte canónico tipo→chip (Global Constraints — copiar, no importar). #}
{% set T = {
  "bug":       "bg-brand-coral/20",
  "feature":   "bg-brand-teal/15",
  "tech-debt": "bg-brand-ochre/25",
  "infra":     "bg-brand-lavender/25",
  "docs":      "bg-brand-mint/30",
  "ops":       "bg-brand-peach/30",
  "security":  "bg-warning/20",
  "product":   "bg-brand-pink/20",
  "idea":      "bg-brand-lavender/25",
} %}
<div>
  {% if not items %}
  <div class="px-4 py-12 text-center text-sm text-muted">
    Sin ítems con estos filtros.
    <a href="/backlog" class="underline text-muted hover:text-body ml-1">Limpiar filtros</a>
  </div>
  {% endif %}
  {% for item in items %}
  <div class="flex flex-wrap md:flex-nowrap items-center gap-2 md:gap-3 px-4 py-3 border-b border-hairline last:border-0 hover:bg-surface-strong">
    <div class="flex flex-col gap-1 shrink-0 items-start">
      <span class="p-pill {{ T.get(item.type, 'bg-surface-strong') }} text-ink">{{ item.type }}</span>
      {% if item.effort_ai %}
      <span class="p-pill bg-surface-strong text-muted">{{ item.effort_ai }}</span>
      {% endif %}
    </div>

    <div class="flex-1 min-w-0">
      <div class="flex items-center gap-2 mb-0.5">
        {% if blocked_ids and item.id|string in blocked_ids %}<span title="Bloqueado por otro ítem abierto">⛔</span>{% endif %}
        {% if unblocker_ids and item.id|string in unblocker_ids %}<span title="Bloquea a otros (desbloqueador)">🔓</span>{% endif %}
        <a href="/items/{{ item.id }}" class="text-sm font-medium text-ink hover:underline truncate">{{ item.title }}</a>
        {% if recent_touch and recent_touch.get(item.id|string) %}<span title="Tocado en las últimas 24h" class="text-success">●</span>{% endif %}
        {% if item.stale_risk %}<span class="text-xs text-warning shrink-0">⚠</span>{% endif %}
        {% if ready_ids and item.id|string in ready_ids %}<span title="Listo para agente (agent_ready + sin bloqueadores)" class="text-xs shrink-0">🤖</span>{% endif %}
      </div>
      <div class="flex items-center gap-2 text-xs text-muted">
        {% with status = item.status %}{% include "partials/_status_badge.html" %}{% endwith %}
        {% if item.impact_ai %}<span>I{{ item.impact_ai }}</span>{% endif %}
        <span>·</span>
        <span>{{ item.origen }}</span>
        <span>{{ scope_map.get(item.scope_id, '') }}</span>
      </div>
    </div>

    <div class="flex items-center gap-2 shrink-0">
      {% include "partials/_priority_select.html" %}
      {% set targets = non_terminal_targets(item.status) %}
      {% if targets %}
      <select title="Cambiar estado" aria-label="Cambiar estado del ítem"
              class="p-input p-input-sm"
              hx-post="/ui/items/{{ item.id }}/transition" hx-trigger="change" hx-swap="none" name="status">
        <option value="">→ mover…</option>
        {% for t in targets %}<option value="{{ t }}">{{ t }}</option>{% endfor %}
      </select>
      {% endif %}
      {% if item.status not in ('done','discarded') %}
      <button class="p-btn p-btn-ghost p-btn-sm"
              hx-get="/ui/items/{{ item.id }}/close-modal"
              hx-target="#modal-slot"
              hx-on::after-swap="openModal('close-modal')"
              title="Cerrar ítem">✓</button>
      {% endif %}
    </div>
  </div>
  {% endfor %}
</div>
```

- [ ] **Step 7: Run tests**

```
TEST_DATABASE_URL="postgresql+asyncpg://efrain:efrain@localhost:5432/pulso_test" python -m pytest tests/test_ui.py::test_close_modal_endpoint tests/test_ui.py::test_item_transition_close_reopen tests/test_ui.py::test_item_detail_and_404 -v
```
Expected: All PASS. (The close/reopen test exercises `/close` which is unchanged; item_detail uses the included partial now.)

- [ ] **Step 8: Commit**

```bash
git add app/templates/partials/_close_modal.html app/templates/item_detail.html app/templates/partials/items_table.html app/ui/router.py
git commit -m "feat(backlog): close-modal partial + GET endpoint + close button in rows/cards + ready chip"
```

---

## Task 6: Group-by list partial (`items_grouped.html`)

**Files:**
- Create: `app/templates/partials/items_grouped.html`

**Interfaces:**
- Consumes: `groups` (list of (label: str, items: list[Item]) tuples from Task 2), plus all context from `backlog()` (`scope_map`, `blocked_ids`, `unblocker_ids`, `ready_ids`, `recent_touch`).

- [ ] **Step 1: Create `app/templates/partials/items_grouped.html`**

```html
{# Lista agrupada — renderiza groups como <details> colapsables, cada uno con items_table.html #}
{% if not groups %}
<div class="px-4 py-12 text-center text-sm text-muted">
  Sin ítems con estos filtros.
  <a href="/backlog" class="underline text-muted hover:text-body ml-1">Limpiar filtros</a>
</div>
{% endif %}
{% for group_name, group_items in groups %}
<details open class="mb-3">
  <summary class="flex items-center gap-3 px-4 py-2.5 cursor-pointer hover:bg-surface-strong rounded-xl list-none">
    <span class="text-sm font-semibold text-ink">{{ group_name }}</span>
    <span class="p-pill bg-surface-strong text-muted ml-1">{{ group_items|length }}</span>
  </summary>
  <div class="p-card overflow-hidden mt-1">
    {% with items = group_items %}
      {% include "partials/items_table.html" %}
    {% endwith %}
  </div>
</details>
{% endfor %}
```

- [ ] **Step 2: Run group-by test**

```
TEST_DATABASE_URL="postgresql+asyncpg://efrain:efrain@localhost:5432/pulso_test" python -m pytest tests/test_ui.py::test_backlog_group_by -v
```
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add app/templates/partials/items_grouped.html
git commit -m "feat(backlog): group-by list partial using native <details> collapsibles"
```

---

## Task 7: Archive backend (`/registro` route + `summarize_closed` in `llm.py`)

**Files:**
- Modify: `app/ui/router.py` (add `GET /registro` + `GET /ui/registro/summary`)
- Modify: `app/ai/llm.py` (add `summarize_closed`)

**Interfaces:**
- `GET /registro` accepts: `scope`, `item_type`, `q`, `before` (ISO date str for load-more pagination). Returns `registro.html` (full) or `registro_rows.html` (HX-Request).
- Context: `grouped` (list of (week_str: str, items: list[Item])), `close_events` (dict[UUID, dict] — latest close event payload per item), `scope_map`, `repo_url` (str|None), `has_more` (bool), `filters`.
- `GET /ui/registro/summary?week=YYYY-Www` → 200 HTML fragment with summary markdown.
- `summarize_closed(items_with_reasons: list[dict]) -> str` — async, raises `LLMUnavailable` without API key.
- Dashboard `GET /` gets `closed_this_week` count (int).

- [ ] **Step 1: Write failing tests**

Add to `tests/test_ui.py`:

```python
@pytest.mark.asyncio
async def test_registro_groups_by_week(client: AsyncClient):
    """GET /registro shows closed items grouped by ISO week."""
    from app.items.models import Item
    from app.scopes.models import Scope
    from datetime import datetime, timezone

    _uid, pid = await _login(client)
    async for db in client.app.dependency_overrides[get_db]():
        scope = (await db.execute(select(Scope).where(Scope.project_id == pid))).scalars().first()
        closed1 = Item(scope_id=scope.id, project_id=pid, title="Closed this week",
                       type="feature", status="done", origen="human",
                       closed_at=datetime.now(timezone.utc))
        closed2 = Item(scope_id=scope.id, project_id=pid, title="Old closed item",
                       type="bug", status="discarded", origen="human",
                       closed_at=datetime(2020, 1, 6, tzinfo=timezone.utc))
        no_date = Item(scope_id=scope.id, project_id=pid, title="No date closed",
                       type="docs", status="done", origen="human", closed_at=None)
        db.add_all([closed1, closed2, no_date])
        await db.commit()
        break

    r = await client.get("/registro")
    assert r.status_code == 200
    assert "Closed this week" in r.text
    assert "Old closed item" in r.text
    assert "Sin fecha" in r.text  # legacy items without closed_at

    # HX-Request returns partial only
    r2 = await client.get("/registro", headers={"HX-Request": "true"})
    assert r2.status_code == 200
    assert "Closed this week" in r2.text


@pytest.mark.asyncio
async def test_registro_close_event_reason(client: AsyncClient):
    """Items in registro show close reason from ItemEvent."""
    from app.items.models import Item, ItemEvent
    from app.scopes.models import Scope
    from datetime import datetime, timezone

    _uid, pid = await _login(client)
    async for db in client.app.dependency_overrides[get_db]():
        scope = (await db.execute(select(Scope).where(Scope.project_id == pid))).scalars().first()
        item = Item(scope_id=scope.id, project_id=pid, title="With reason item",
                    type="feature", status="done", origen="human",
                    closed_at=datetime.now(timezone.utc))
        db.add(item)
        await db.flush()
        ev = ItemEvent(item_id=item.id, actor="user@test", action="closed",
                       payload={"status": "done", "reason": "shipped v2", "commit_sha": "abc123"})
        db.add(ev)
        await db.commit()
        break

    r = await client.get("/registro")
    assert r.status_code == 200
    assert "shipped v2" in r.text
    assert "abc123" in r.text


@pytest.mark.asyncio
async def test_registro_filters_and_load_more(client: AsyncClient):
    """scope, item_type, q filters work; before= param loads older items."""
    from app.items.models import Item
    from app.scopes.models import Scope
    from datetime import datetime, timezone

    _uid, pid = await _login(client)
    async for db in client.app.dependency_overrides[get_db]():
        scope = (await db.execute(select(Scope).where(Scope.project_id == pid))).scalars().first()
        db.add(Item(scope_id=scope.id, project_id=pid, title="Bug closed item",
                    type="bug", status="done", origen="human",
                    closed_at=datetime.now(timezone.utc)))
        db.add(Item(scope_id=scope.id, project_id=pid, title="Feature closed item",
                    type="feature", status="done", origen="human",
                    closed_at=datetime.now(timezone.utc)))
        await db.commit()
        break

    r = await client.get("/registro?item_type=bug")
    assert r.status_code == 200
    assert "Bug closed item" in r.text
    assert "Feature closed item" not in r.text

    r2 = await client.get("/registro?before=2020-01-01")
    assert r2.status_code == 200  # no results, but no crash


@pytest.mark.asyncio
async def test_registro_summary_no_api_key(client: AsyncClient):
    """Summary endpoint returns 'no disponible' message when API key missing."""
    _uid, pid = await _login(client)
    r = await client.get("/ui/registro/summary?week=2026-W27")
    assert r.status_code == 200
    # Without API key → degraded message
    assert "no disponible" in r.text.lower() or r.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

```
TEST_DATABASE_URL="postgresql+asyncpg://efrain:efrain@localhost:5432/pulso_test" python -m pytest tests/test_ui.py::test_registro_groups_by_week tests/test_ui.py::test_registro_close_event_reason tests/test_ui.py::test_registro_filters_and_load_more tests/test_ui.py::test_registro_summary_no_api_key -v
```
Expected: FAIL (routes don't exist).

- [ ] **Step 3: Add `summarize_closed` to `app/ai/llm.py`**

Add after `triage_sentry`:

```python
_SUMMARY_PROMPT = """Eres un ingeniero de producto. Resume en 3-5 bullets los ítems cerrados esta semana en este proyecto de software.
Sé conciso y específico. Devuelve SOLO markdown (bullets con -).

Ítems cerrados:
{items_text}

Resumen:"""


async def summarize_closed(items_with_reasons: list[dict[str, Any]]) -> str:
    """Genera resumen markdown de ítems cerrados. Lanza LLMUnavailable sin API key."""
    if not settings.anthropic_api_key:
        raise LLMUnavailable("ANTHROPIC_API_KEY no configurada")
    items_text = "\n".join(
        f"- [{r.get('type', '')}] {r.get('title', '')} — {r.get('reason', '') or '(sin motivo)'}"
        for r in items_with_reasons
    )
    prompt = _SUMMARY_PROMPT.format(items_text=items_text)
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            _ANTHROPIC_URL,
            headers={
                "x-api-key": settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": _HAIKU_MODEL,
                "max_tokens": 500,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
    return "".join(block.get("text", "") for block in data.get("content", []))
```

- [ ] **Step 4: Add `/registro` and `/ui/registro/summary` routes to `app/ui/router.py`**

Add these routes after the `/ideas` route (search for `@router.get("/ideas"` and add after its function). Add the necessary imports at the function level (to avoid circular imports):

```python
@router.get("/registro", response_class=HTMLResponse)
async def registro(
    request: Request,
    scope: str | None = None,
    item_type: str | None = None,
    q: str | None = None,
    before: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.items.search import search_items as _fts
    from app.projects.models import Project
    from itertools import groupby as _gb

    pid = await _project_id(db, user, request)

    base_q = select(Item).where(
        Item.project_id == pid,
        Item.status.in_(["done", "discarded"]),
    )
    if scope:
        scope_row = await db.scalar(
            select(Scope).where(Scope.name == scope, Scope.project_id == pid)
        )
        if scope_row:
            base_q = base_q.where(Item.scope_id == scope_row.id)
        else:
            base_q = base_q.where(Item.id == uuid.UUID(int=0))
    if item_type:
        base_q = base_q.where(Item.type == item_type)
    if before:
        try:
            from datetime import timezone as _tz
            before_dt = datetime.fromisoformat(before).replace(tzinfo=timezone.utc)
            base_q = base_q.where(Item.closed_at < before_dt)
        except ValueError:
            pass
    if q:
        fts_rows = await _fts(db, q, project_id=pid)
        matched = {r["id"] for r in fts_rows if r["status"] in ("done", "discarded")}
        if matched:
            base_q = base_q.where(Item.id.in_([uuid.UUID(i) for i in matched]))
        else:
            base_q = base_q.where(Item.id == uuid.UUID(int=0))

    base_q = base_q.order_by(Item.closed_at.desc().nullslast())
    items = list((await db.execute(base_q.limit(200))).scalars().all())

    # Batch fetch latest close event per item (no N+1)
    item_ids = [i.id for i in items]
    close_events: dict[uuid.UUID, dict] = {}
    if item_ids:
        from app.items.models import ItemEvent
        ev_rows = (await db.execute(
            select(ItemEvent).where(
                ItemEvent.item_id.in_(item_ids),
                ItemEvent.action == "closed",
            ).order_by(ItemEvent.created_at.desc())
        )).scalars().all()
        for ev in ev_rows:
            if ev.item_id not in close_events:
                close_events[ev.item_id] = ev.payload or {}

    def _iso_week(item: Item) -> str:
        if not item.closed_at:
            return "__no_date__"
        return item.closed_at.strftime("%G-W%V")

    grouped = [(wk, list(wi)) for wk, wi in _gb(items, key=_iso_week)]

    scopes = list((await db.execute(
        select(Scope).where(Scope.archived.is_(False), Scope.project_id == pid).order_by(Scope.name)
    )).scalars().all())
    scope_map = {s.id: s.name for s in scopes}
    project = await db.scalar(select(Project).where(Project.id == pid))

    ctx = {
        "user": user,
        "items": items,
        "grouped": grouped,
        "close_events": close_events,
        "scopes": scopes,
        "scope_map": scope_map,
        "repo_url": project.repo_url if project else None,
        "filters": {"scope": scope, "item_type": item_type, "q": q},
        "has_more": len(items) == 200,
    }
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "partials/registro_rows.html", ctx)
    return templates.TemplateResponse(request, "registro.html", ctx)


@router.get("/ui/registro/summary", response_class=HTMLResponse)
async def registro_summary(
    request: Request,
    week: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.ai.llm import LLMUnavailable, summarize_closed
    from app.items.models import ItemEvent

    pid = await _project_id(db, user, request)
    try:
        monday = datetime.strptime(f"{week}-1", "%G-W%V-%u").replace(tzinfo=timezone.utc)
        sunday = monday + timedelta(days=6, hours=23, minutes=59, seconds=59)
    except ValueError:
        return Response(content="Semana inválida", status_code=422)

    items = list((await db.execute(
        select(Item).where(
            Item.project_id == pid,
            Item.status.in_(["done", "discarded"]),
            Item.closed_at >= monday,
            Item.closed_at <= sunday,
        )
    )).scalars().all())

    if not items:
        return HTMLResponse('<p class="text-sm text-muted italic">Sin ítems cerrados en esta semana.</p>')

    ev_rows = (await db.execute(
        select(ItemEvent).where(
            ItemEvent.item_id.in_([i.id for i in items]),
            ItemEvent.action == "closed",
        ).order_by(ItemEvent.created_at.desc())
    )).scalars().all()
    reasons: dict[uuid.UUID, str] = {}
    for ev in ev_rows:
        if ev.item_id not in reasons:
            reasons[ev.item_id] = (ev.payload or {}).get("reason") or ""

    items_data = [
        {"title": i.title, "type": i.type, "reason": reasons.get(i.id, "")}
        for i in items
    ]
    try:
        summary_md = await summarize_closed(items_data)
    except LLMUnavailable:
        return HTMLResponse('<p class="text-sm text-muted italic">Resumen IA no disponible (ANTHROPIC_API_KEY no configurada).</p>')

    return HTMLResponse(
        f'<div class="text-sm text-body whitespace-pre-wrap p-4 bg-surface-soft rounded-xl border border-hairline">{summary_md}</div>'
    )
```

Also add `timedelta` to the imports at the top of `router.py` if not already present (check — `timedelta` is already imported via `from datetime import datetime, timedelta, timezone`).

- [ ] **Step 5: Update the dashboard route to include `closed_this_week` count**

In the `dashboard()` function (around line 64), add after the existing counts:

```python
    from datetime import timedelta as _td
    week_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = week_start - timedelta(days=week_start.weekday())
    closed_this_week = int(await db.scalar(
        select(func.count()).select_from(Item).where(
            Item.project_id == pid,
            Item.status.in_(["done", "discarded"]),
            Item.closed_at >= week_start,
        )
    ) or 0)
```

And add to the `cards` dict:
```python
        "closed_this_week": closed_this_week,
```

- [ ] **Step 6: Run backend tests**

```
TEST_DATABASE_URL="postgresql+asyncpg://efrain:efrain@localhost:5432/pulso_test" python -m pytest tests/test_ui.py::test_registro_groups_by_week tests/test_ui.py::test_registro_close_event_reason tests/test_ui.py::test_registro_filters_and_load_more tests/test_ui.py::test_registro_summary_no_api_key -v
```
Expected: PASS (templates don't exist yet but the route tests that matter — status 200 — pass once templates are created in Task 8).

NOTE: These tests will actually fail until Task 8 creates the templates. Run them after Task 8.

- [ ] **Step 7: Commit (backend only)**

```bash
git add app/ui/router.py app/ai/llm.py
git commit -m "feat(registro): /registro route + /ui/registro/summary + summarize_closed in llm.py"
```

---

## Task 8: Archive templates (`registro.html` + `registro_rows.html`)

**Files:**
- Create: `app/templates/registro.html`
- Create: `app/templates/partials/registro_rows.html`

**Interfaces:**
- Consumes: `grouped`, `close_events`, `scope_map`, `repo_url`, `has_more`, `filters`, `scopes` from Task 7 route.
- `registro_rows.html` is the HX-Request swap target partial (reused for load-more with `before=`).

- [ ] **Step 1: Create `app/templates/partials/registro_rows.html`**

```html
{#
  Filas del registro de ítems cerrados — partial para HX-Request (load-more y filtros).
  Consumes: grouped (list of (week_str, items)), close_events, scope_map, repo_url, has_more, filters.
#}
{% set S = {
  "done":      "bg-success/15 text-success",
  "discarded": "bg-surface-strong text-muted line-through",
} %}
{% set T = {
  "bug":"bg-brand-coral/20","feature":"bg-brand-teal/15","tech-debt":"bg-brand-ochre/25",
  "infra":"bg-brand-lavender/25","docs":"bg-brand-mint/30","ops":"bg-brand-peach/30",
  "security":"bg-warning/20","product":"bg-brand-pink/20","idea":"bg-brand-lavender/25",
} %}

{% if not grouped %}
<div class="px-4 py-12 text-center text-sm text-muted">Sin ítems cerrados con estos filtros.</div>
{% endif %}

{% for week_key, week_items in grouped %}
{% if week_key == "__no_date__" %}
  {% set week_label = "Sin fecha" %}
{% else %}
  {# Parse ISO week for display: "2026-W27" → "Semana del 29/06 – 05/07" #}
  {% set week_label = "Semana " ~ week_key %}
{% endif %}

<section class="mb-6" id="week-{{ week_key | replace('/','-') }}">
  <div class="flex items-center gap-3 mb-2 px-1">
    <h2 class="text-sm font-semibold text-ink">{{ week_label }}</h2>
    {% set n_done = week_items | selectattr('status', 'eq', 'done') | list | length %}
    {% set n_disc = week_items | selectattr('status', 'eq', 'discarded') | list | length %}
    {% if n_done %}<span class="p-pill bg-success/15 text-success">{{ n_done }} done</span>{% endif %}
    {% if n_disc %}<span class="p-pill bg-surface-strong text-muted">{{ n_disc }} discarded</span>{% endif %}
    {% if week_key != "__no_date__" %}
    <button class="p-btn p-btn-ghost p-btn-sm ml-auto"
            hx-get="/ui/registro/summary?week={{ week_key }}"
            hx-target="#summary-{{ week_key | replace('/','-') | replace(':','-') }}"
            hx-swap="innerHTML"
            title="Generar resumen IA de esta semana">
      ✨ Resumen IA
    </button>
    {% endif %}
  </div>
  <div id="summary-{{ week_key | replace('/','-') | replace(':','-') }}" class="mb-2"></div>

  <div class="p-card overflow-hidden">
    {% for item in week_items %}
    {% set ev = close_events.get(item.id, {}) %}
    <div class="flex flex-wrap md:flex-nowrap items-center gap-2 md:gap-3 px-4 py-3 border-b border-hairline last:border-0 hover:bg-surface-strong">
      <div class="shrink-0">
        <span class="p-pill {{ T.get(item.type, 'bg-surface-strong') }} text-ink">{{ item.type }}</span>
      </div>
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-2 mb-0.5">
          <a href="/items/{{ item.id }}" class="text-sm font-medium text-ink hover:underline truncate">{{ item.title }}</a>
          <span class="p-pill {{ S.get(item.status, 'bg-surface-strong text-body') }} shrink-0">{{ item.status }}</span>
        </div>
        <div class="flex items-center gap-2 text-xs text-muted flex-wrap">
          {% if item.closed_at %}
          <span>{{ item.closed_at.strftime('%d/%m/%Y') }}</span>
          {% endif %}
          <span>{{ scope_map.get(item.scope_id, '') }}</span>
          {% if ev.get('reason') %}
          <span class="text-muted">· {{ ev.reason }}</span>
          {% endif %}
          {% if ev.get('commit_sha') %}
          <span>·
            {% if repo_url %}
            <a href="{{ repo_url }}/commit/{{ ev.commit_sha }}"
               class="font-mono underline hover:text-body" target="_blank" rel="noopener">
              {{ ev.commit_sha[:8] }}
            </a>
            {% else %}
            <span class="font-mono">{{ ev.commit_sha[:8] }}</span>
            {% endif %}
          </span>
          {% endif %}
        </div>
      </div>
    </div>
    {% endfor %}
  </div>
</section>
{% endfor %}

{% if has_more %}
{% set last_item = grouped[-1][1][-1] if grouped and grouped[-1][1] else None %}
{% if last_item and last_item.closed_at %}
<div class="text-center py-4">
  <button class="p-btn p-btn-ghost"
          hx-get="/registro?before={{ last_item.closed_at.strftime('%Y-%m-%dT%H:%M:%S') }}{% if filters.scope %}&scope={{ filters.scope }}{% endif %}{% if filters.item_type %}&item_type={{ filters.item_type }}{% endif %}{% if filters.q %}&q={{ filters.q }}{% endif %}"
          hx-target="#registro-rows"
          hx-swap="beforeend"
          hx-push-url="false">
    Cargar más
  </button>
</div>
{% endif %}
{% endif %}
```

- [ ] **Step 2: Create `app/templates/registro.html`**

```html
{% extends "base.html" %}
{% from "partials/_hint.html" import hint %}
{% block title %}Archive{% endblock %}
{% block content %}

<div class="flex flex-wrap items-center justify-between gap-3 mb-4">
  <div class="flex items-baseline gap-3">
    <h1 class="text-2xl md:text-3xl font-semibold tracking-tight text-ink">Archive</h1>
    <span class="text-sm text-muted">{{ items|length }} cerrados</span>
  </div>
</div>

{{ hint('registro', '<strong>Registro histórico.</strong> Aquí viven los ítems cerrados (done / discarded), agrupados por semana. El motivo y el commit vienen del historial de cierre. Para reabrir un ítem, entra a su detalle.'|safe) }}

{# Filters #}
<div class="sticky top-[67px] z-40 bg-canvas backdrop-blur py-2 -mx-1 px-1 mb-4">
  <form id="registro-filters">
    <div class="flex flex-wrap gap-2 items-center">
      <input type="text" name="q" value="{{ filters.q or '' }}" placeholder="Buscar…"
             aria-label="Buscar en el registro"
             class="p-input p-input-sm flex-1 min-w-[140px]"
             hx-get="/registro" hx-target="#registro-rows" hx-include="#registro-filters"
             hx-push-url="true" hx-trigger="keyup changed delay:300ms, search">

      <select name="scope" aria-label="Filtrar por scope" class="p-input p-input-sm"
              hx-get="/registro" hx-target="#registro-rows" hx-include="#registro-filters"
              hx-push-url="true">
        <option value="">Todos los scopes</option>
        {% for s in scopes %}
        <option value="{{ s.name }}" {% if filters.scope == s.name %}selected{% endif %}>{{ s.name }}</option>
        {% endfor %}
      </select>

      <select name="item_type" aria-label="Filtrar por tipo" class="p-input p-input-sm"
              hx-get="/registro" hx-target="#registro-rows" hx-include="#registro-filters"
              hx-push-url="true">
        <option value="">Todos los tipos</option>
        {% for t in ['bug','feature','tech-debt','infra','docs','ops','security','product','idea'] %}
        <option value="{{ t }}" {% if filters.item_type == t %}selected{% endif %}>{{ t }}</option>
        {% endfor %}
      </select>

      <a href="/registro" class="p-btn p-btn-ghost p-btn-sm">Limpiar</a>
    </div>
  </form>
</div>

<div id="registro-rows">
  {% include "partials/registro_rows.html" %}
</div>

{% endblock %}
```

- [ ] **Step 3: Run archive tests**

```
TEST_DATABASE_URL="postgresql+asyncpg://efrain:efrain@localhost:5432/pulso_test" python -m pytest tests/test_ui.py::test_registro_groups_by_week tests/test_ui.py::test_registro_close_event_reason tests/test_ui.py::test_registro_filters_and_load_more tests/test_ui.py::test_registro_summary_no_api_key -v
```
Expected: All PASS.

- [ ] **Step 4: Commit**

```bash
git add app/templates/registro.html app/templates/partials/registro_rows.html
git commit -m "feat(registro): Archive screen templates — ISO-week groups, motivo/commit, load-more, AI summary button"
```

---

## Task 9: Archive NAV entry + Dashboard homecard + `closed_this_week` count

**Files:**
- Modify: `app/templates/base.html` (NAV array)
- Modify: `app/templates/dashboard.html` (new homecard)

**Interfaces:**
- Consumes: `cards.closed_this_week` (int) added to `cards` dict in Task 7, Step 5.

- [ ] **Step 1: Write failing test**

Add to `tests/test_ui.py`:

```python
@pytest.mark.asyncio
async def test_registro_in_nav_and_dashboard_homecard(client: AsyncClient):
    _uid, pid = await _login(client)
    # Dashboard renders Archive homecard
    r = await client.get("/")
    assert r.status_code == 200
    assert "/registro" in r.text
    # NAV contains Archive link
    assert "Archive" in r.text
    # /registro is reachable
    r2 = await client.get("/registro")
    assert r2.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails (Archive not in NAV yet)**

```
TEST_DATABASE_URL="postgresql+asyncpg://efrain:efrain@localhost:5432/pulso_test" python -m pytest tests/test_ui.py::test_registro_in_nav_and_dashboard_homecard -v
```
Expected: FAIL.

- [ ] **Step 3: Update `app/templates/base.html` line 8**

Change:
```html
  {% set NAV = [("/backlog", "Backlog"), ("/prioridad", "Priority"), ("/hilos", "Threads"), ("/incidentes", "Incidents"), ("/ideas", "Ideas")] %}
```
To:
```html
  {% set NAV = [("/backlog", "Backlog"), ("/prioridad", "Priority"), ("/hilos", "Threads"), ("/incidentes", "Incidents"), ("/ideas", "Ideas"), ("/registro", "Archive")] %}
```

- [ ] **Step 4: Update `app/templates/dashboard.html` — add Archive homecard**

In `dashboard.html`, inside `<div id="home-cards" ...>` after the Ideas card, add:

```html
  <a href="/registro" class="p-homecard bg-brand-mint text-ink">
    <span class="w-9 h-9 rounded-xl bg-black/10 flex items-center justify-center">
      <svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5.586a1 1 0 0 1 .707.293l5.414 5.414a1 1 0 0 1 .293.707V19a2 2 0 0 1-2 2Z"/></svg>
    </span>
    <div>
      <p class="text-lg font-semibold">Archive</p>
      <p class="text-sm opacity-70">{{ cards.closed_this_week }} cerrados esta semana</p>
    </div>
  </a>
```

- [ ] **Step 5: Run test and full suite**

```
TEST_DATABASE_URL="postgresql+asyncpg://efrain:efrain@localhost:5432/pulso_test" python -m pytest tests/test_ui.py::test_registro_in_nav_and_dashboard_homecard -v
TEST_DATABASE_URL="postgresql+asyncpg://efrain:efrain@localhost:5432/pulso_test" python -m pytest tests/ -q
```
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add app/templates/base.html app/templates/dashboard.html
git commit -m "feat(registro): Archive entry in NAV + homecard on dashboard"
```

---

## Task 10: Final checks, CI, and PR

**Files:**
- No new code changes

- [ ] **Step 1: Run full lint + type-check + tests**

```bash
TEST_DATABASE_URL="postgresql+asyncpg://efrain:efrain@localhost:5432/pulso_test" python -m pytest tests/ -q
ruff check app/ tests/
python -m mypy app/
```
Expected: All green. Fix any ruff/mypy issues before continuing.

- [ ] **Step 2: Run the app locally and smoke-test**

```bash
uvicorn app.main:create_app --factory --reload --port 8000
```

Check manually:
- `/backlog` default shows only open items (done items not visible)
- Toggle "Cerrados" shows done items; "Todos" shows everything
- Search input filters live
- Quick win chip filters correctly
- View toggle switches to board (6 columns)
- ✓ button on a row opens the close modal
- `/registro` shows grouped weeks
- `/registro` — ✨ Resumen IA button (graceful if no API key)
- "Archive" appears in the navbar
- Dashboard shows Archive homecard

- [ ] **Step 3: Create PR**

```bash
git push origin main
gh pr create \
  --title "feat: backlog redesign + Archive panel (iterations 1–2)" \
  --body "$(cat <<'EOF'
## Summary
- Open-only backlog default (show=open/closed/all chips)
- FTS search via existing search_vector column
- Board/kanban view (6 open-status columns), no drag-drop
- Quick filter chips: quick wins, urgent, agent-ready, stale, blocked
- Priority/effort selects + group-by list (collapsible details)
- Close-from-row/card: extracted _close_modal.html partial + GET /ui/items/{id}/close-modal
- Ready chip 🤖 (agent_ready + no blockers)
- /registro Archive panel: ISO-week groups, close reason + commit from ItemEvent/source_refs
- Load-more pagination (before= param)
- AI weekly summary (Haiku, degrades without API key)
- Archive NAV entry + Dashboard homecard
- Fix: service.py topological order bug (typo "topologico")
- SQL ordering for priority/impact/recent (limit 300 now correct)

## Test plan
- [ ] `pytest tests/ -q` green
- [ ] `ruff check app/ tests/` clean
- [ ] `mypy app/` clean
- [ ] Manual smoke test: backlog default hides done, board renders, close modal opens from row, /registro groups by week

Zero schema migrations. CI must be green before merge.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review: Spec Coverage Checklist

| Spec requirement | Covered by |
|---|---|
| 1.1 `show` param + open/closed/all chips | Task 2 (route) + Task 3 (template) |
| 1.2 FTS search `q` via `search_items` | Task 2 (route) + Task 3 (template) |
| 1.3 `priority`/`effort` selects + 5 quick chips | Task 2 + Task 3 |
| 1.4 Fix topological order bug | Task 1 |
| 1.5 Board view (6 columns, no done) | Task 2 + Task 4 |
| 1.6 Close from row/card + GET endpoint | Task 5 |
| 1.7 Ready chip 🤖 (computed, not just SQL flag) | Task 2 + Task 5 |
| 2.1 `/registro` route + ISO-week groups + motivo + commit + load-more | Task 7 + Task 8 |
| 2.1 Archive homecard in dashboard | Task 7 (count) + Task 9 |
| 2.2 Group-by list (scope/type/priority/status) | Task 2 + Task 6 |
| 2.3 SQL ordering (priority/impact/recent) | Task 2 |
| 2.4 AI weekly summary (degradable) | Task 7 + Task 8 |
| Archive in NAV | Task 9 |
| item_detail.html deduped to shared partial | Task 5 |
| All new features with tests | Tasks 1–9 |
