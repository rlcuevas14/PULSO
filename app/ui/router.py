import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.deps import current_user_ui
from app.auth.models import ApiToken, User
from app.database import get_db
from app.items import graph, service
from app.items.lifecycle import allowed_targets, non_terminal_targets
from app.items.models import Item
from app.jobs.models import AgentRun
from app.projects.access import resolve_current_project, user_role_on_project
from app.scopes.models import Scope
from app.templates_config import templates
from app.ui.flash import flash_success

router = APIRouter(tags=["ui"])

_OPEN = ["idea", "backlog", "spec", "in-progress", "blocked", "in-review"]
_PRIORITY_RANK = {"p0": 0, "p1": 1, "p2": 2, "p3": 3, None: 9}


def _recent_touch(item: Item) -> bool:
    if item.last_touched_at is None:
        return False
    return item.last_touched_at > datetime.now(timezone.utc) - timedelta(hours=24)


async def _project_id(db: AsyncSession, user: User, request: Request) -> uuid.UUID:
    """Current project for UI list screens; a zero-UUID sentinel (matches nothing) when the
    user can reach no project — so they see an empty board, never another account's data."""
    p = await resolve_current_project(db, user, request)
    return p.id if p else uuid.UUID(int=0)


async def _guard_row(
    db: AsyncSession, user: User, project_id, *, write: bool = False
) -> Response | None:
    """404 if the row's project isn't accessible to the user; 403 if a viewer attempts a write."""
    role = await user_role_on_project(db, user, project_id)
    if role is None:
        return Response(status_code=404)
    if write and role == "viewer":
        return Response(content="Viewer cannot modify this project", status_code=403)
    return None


# ---------- Tablero ----------

@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.threads.models import Thread
    from app.webhooks.models import SentryIssue

    pid = await _project_id(db, user, request)
    counts_q = await db.execute(
        select(Item.status, func.count().label("n"))
        .where(Item.project_id == pid).group_by(Item.status)
    )
    counts = {row.status: row.n for row in counts_q}
    blocked_ids = await graph.graph_blocked_ids(db, project_id=pid)

    quick_wins_n = int(await db.scalar(
        select(func.count()).select_from(Item).where(
            Item.project_id == pid, Item.impact_ai >= 4,
            Item.effort_ai.in_(["XS", "S"]), Item.status.not_in(["done", "discarded"]),
        )
    ) or 0)
    threads_active = int(await db.scalar(
        select(func.count()).select_from(Thread).where(
            Thread.project_id == pid, Thread.stage.not_in(["hecho", "descartado"]),
        )
    ) or 0)
    incidents_new = int(await db.scalar(
        select(func.count()).select_from(SentryIssue).where(
            SentryIssue.project_id == pid, SentryIssue.status == "new",
        )
    ) or 0)

    recent_q = await db.execute(
        select(Item).where(Item.project_id == pid).order_by(Item.created_at.desc()).limit(10)
    )
    recent = recent_q.scalars().all()
    cost_q = await db.scalar(
        select(func.sum(AgentRun.cost_usd)).where(AgentRun.status == "ok", AgentRun.project_id == pid)
    )
    scopes = list((await db.execute(
        select(Scope).where(Scope.archived.is_(False), Scope.project_id == pid).order_by(Scope.name)
    )).scalars().all())

    cards = {
        "open": sum(counts.get(s, 0) for s in ("backlog", "spec", "in-progress", "blocked", "in-review")),
        "in_progress": counts.get("in-progress", 0),
        "blocked": len(blocked_ids),
        "quick_wins": quick_wins_n,
        "threads_active": threads_active,
        "incidents_new": incidents_new,
        "ideas": counts.get("idea", 0),
    }
    return templates.TemplateResponse(
        request, "dashboard.html",
        {
            "user": user, "cards": cards, "recent": recent,
            "recent_touch": {str(i.id): _recent_touch(i) for i in recent},
            "monthly_cost": float(cost_q or 0), "scopes": scopes,
        },
    )


# ---------- Backlog ----------

_BOARD_STATUSES = ["idea", "backlog", "spec", "in-progress", "in-review", "blocked"]


@router.get("/backlog", response_class=HTMLResponse)
async def backlog(
    request: Request,
    # existing params
    scope: str | None = None,
    status: str | None = None,
    item_type: str | None = None,
    origen: str | None = None,
    stale: bool | None = None,
    graph_blocked: bool | None = None,
    order: str = "priority",
    # new params
    show: str = "open",         # "open" | "all" | "closed"
    q: str | None = None,       # FTS search
    priority: str | None = None,
    effort: str | None = None,
    quickwins: bool = False,
    urgent: bool = False,
    agent_ready: bool = False,
    view: str = "list",         # "list" | "board"
    group: str = "",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from sqlalchemy import case as sa_case

    from app.items.search import search_items as _fts

    pid = await _project_id(db, user, request)
    q_base = select(Item).where(Item.project_id == pid)

    # status / show filter
    if status:
        q_base = q_base.where(Item.status == status)
    elif view == "board":
        q_base = q_base.where(Item.status.in_(_BOARD_STATUSES))
    elif show == "open":
        q_base = q_base.where(Item.status.in_(_OPEN))
    elif show == "closed":
        q_base = q_base.where(Item.status.in_(["done", "discarded"]))
    # show == "all": no filter

    if scope:
        scope_row = await db.scalar(
            select(Scope).where(Scope.name == scope, Scope.project_id == pid)
        )
        if scope_row:
            q_base = q_base.where(Item.scope_id == scope_row.id)
        else:
            q_base = q_base.where(Item.id == uuid.UUID(int=0))

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

    # SQL ordering — topological stays in Python
    if order in ("priority", "prioridad"):
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
        q_base = q_base.order_by(Item.impact_ai.desc().nullslast())
    elif order == "recent":
        q_base = q_base.order_by(Item.created_at.desc())

    q_base = q_base.limit(300)
    items = list((await db.execute(q_base)).scalars().all())

    # FTS search (post-filter by ids to combine with other SQL filters)
    if q:
        fts_rows = await _fts(db, q, project_id=pid)
        matched = {r["id"] for r in fts_rows}
        items = [i for i in items if str(i.id) in matched]

    blocked_ids = await graph.graph_blocked_ids(db, project_id=pid)
    unblocker_ids = await graph.unblocker_ids(db, project_id=pid)

    if graph_blocked:
        items = [i for i in items if str(i.id) in blocked_ids]

    # Topological ordering (Python; needs graph)
    if order in ("topological", "topologico"):
        topo = await _topo_order_ids(db, items)
        items = _order_items(items, "topological", topo)

    # ready_ids: agent_ready + open state + not graph-blocked
    ready_ids = {
        str(i.id) for i in items
        if i.agent_ready and i.status in ("backlog", "spec") and str(i.id) not in blocked_ids
    }

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

    # Board context
    if view == "board":
        by_status: dict[str, list] = {s: [] for s in _BOARD_STATUSES}
        for item in items:
            if item.status in by_status:
                by_status[item.status].append(item)
        ctx["by_status"] = by_status
        ctx["board_statuses"] = _BOARD_STATUSES

    # Group-by context
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


def _order_items(items: list[Item], order: str, topo_rank: dict[str, int] | None) -> list[Item]:
    if order in ("impact", "impacto"):
        return sorted(items, key=lambda i: (-(i.impact_ai or 0), i.effort_ai or "ZZ"))
    if order in ("priority", "prioridad"):
        return sorted(items, key=lambda i: (_PRIORITY_RANK.get(i.priority, 9), -(i.impact_ai or 0)))
    if order in ("topological", "topologico") and topo_rank is not None:
        return sorted(items, key=lambda i: topo_rank.get(str(i.id), 1_000_000))
    return sorted(items, key=lambda i: i.created_at, reverse=True)


async def _topo_order_ids(db: AsyncSession, items: list[Item]) -> dict[str, int]:
    ids = [str(i.id) for i in items]
    if not ids:
        return {}
    rels = await db.execute(
        text("""
            SELECT source_id, target_id, relation FROM item_relationships
            WHERE source_id = ANY(:ids) AND target_id = ANY(:ids)
        """),
        {"ids": ids},
    )
    edges = [(str(r["source_id"]), str(r["target_id"]), r["relation"]) for r in rels.mappings().all()]
    impact = {str(i.id): (i.impact_ai or 0) for i in items}
    result = graph.topological_order(ids, edges, impact)
    return {item_id: rank for rank, item_id in enumerate(result["order"])}


# ---------- Detalle de ítem ----------

@router.get("/items/{item_id}", response_class=HTMLResponse)
async def item_detail(
    item_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    result = await db.execute(
        select(Item).where(Item.id == item_id).options(
            selectinload(Item.comments), selectinload(Item.events), selectinload(Item.enrichments),
        )
    )
    item = result.scalar_one_or_none()
    if item is None:
        return Response(status_code=404, content="Item no encontrado")
    guard = await _guard_row(db, user, item.project_id)
    if guard is not None:
        return guard

    scope = await db.scalar(select(Scope).where(Scope.id == item.scope_id))
    blockers = await graph.blockers_of(db, item.id)
    sub = await graph.subgraph(db, item.id)
    scopes = list((await db.execute(
        select(Scope).where(Scope.archived.is_(False), Scope.project_id == item.project_id)
        .order_by(Scope.name)
    )).scalars().all())

    return templates.TemplateResponse(
        request,
        "item_detail.html",
        {
            "user": user, "item": item, "scope": scope, "scopes": scopes,
            "transitions": non_terminal_targets(item.status),
            "all_targets": allowed_targets(item.status),
            "blockers": blockers,
            "subgraph": sub,
        },
    )


# ---------- Prioridad (matriz impacto × esfuerzo) ----------

@router.get("/prioridad", response_class=HTMLResponse)
async def prioridad_page(
    request: Request,
    scope: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    pid = await _project_id(db, user, request)
    q = select(Item).where(Item.status.in_(_OPEN), Item.project_id == pid)
    if scope:
        scope_row = await db.scalar(select(Scope).where(Scope.name == scope, Scope.project_id == pid))
        if scope_row:
            q = q.where(Item.scope_id == scope_row.id)
    items = list((await db.execute(q.limit(500))).scalars().all())

    # Celdas de la matriz: impacto (5..1) × esfuerzo (XS..XL).
    efforts = ["XS", "S", "M", "L", "XL"]
    matrix: dict[tuple[int, str], list[Item]] = {}
    unestimated: list[Item] = []
    for it in items:
        if it.impact_ai and it.effort_ai:
            matrix.setdefault((it.impact_ai, it.effort_ai), []).append(it)
        else:
            unestimated.append(it)

    ranked = sorted(items, key=lambda i: (_PRIORITY_RANK.get(i.priority, 9), -(i.impact_ai or 0)))
    scopes = list((await db.execute(
        select(Scope).where(Scope.archived.is_(False), Scope.project_id == pid).order_by(Scope.name)
    )).scalars().all())

    ctx = {
        "user": user, "items": ranked, "matrix": matrix, "efforts": efforts,
        "impacts": [5, 4, 3, 2, 1], "unestimated": unestimated,
        "scopes": scopes, "filters": {"scope": scope},
    }
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "partials/prioridad_body.html", ctx)
    return templates.TemplateResponse(request, "prioridad.html", ctx)


# ---------- Acciones de UI (HTMX, form-encoded) ----------

def _refresh() -> Response:
    return Response(status_code=204, headers={"HX-Refresh": "true"})


@router.post("/ui/items/{item_id}/transition")
async def ui_transition(
    item_id: uuid.UUID,
    status: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    item = await service.get_item(db, item_id)
    if item is None:
        return Response(status_code=404)
    guard = await _guard_row(db, user, item.project_id, write=True)
    if guard is not None:
        return guard
    try:
        await service.apply_transition(db, item, status, user.email)
        await db.commit()
    except service.TransitionError as e:
        return Response(content=str(e), status_code=422)
    return _refresh()


@router.post("/ui/items/{item_id}/close")
async def ui_close(
    item_id: uuid.UUID,
    request: Request,
    status: str = Form(...),
    reason: str = Form(""),
    commit_sha: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    item = await service.get_item(db, item_id)
    if item is None:
        return Response(status_code=404)
    guard = await _guard_row(db, user, item.project_id, write=True)
    if guard is not None:
        return guard
    try:
        await service.close_item(db, item, status, reason or None, user.email, commit_sha or None)
        await db.commit()
    except service.TransitionError as e:
        return Response(content=str(e), status_code=422)
    if status == "done":
        flash_success(request, title=item.title, celebrate=True)
    else:
        flash_success(request, message="Ítem descartado")
    return _refresh()


@router.post("/ui/items/{item_id}/reopen")
async def ui_reopen(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    item = await service.get_item(db, item_id)
    if item is None:
        return Response(status_code=404)
    guard = await _guard_row(db, user, item.project_id, write=True)
    if guard is not None:
        return guard
    try:
        await service.reopen_item(db, item, user.email)
        await db.commit()
    except service.TransitionError as e:
        return Response(content=str(e), status_code=422)
    return _refresh()


@router.post("/ui/items/{item_id}/field")
async def ui_set_field(
    item_id: uuid.UUID,
    field: str = Form(...),
    value: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    item = await service.get_item(db, item_id)
    if item is None:
        return Response(status_code=404)
    guard = await _guard_row(db, user, item.project_id, write=True)
    if guard is not None:
        return guard
    if field == "priority":
        await service.set_priority(db, item, value or None, user.email)
    elif field == "impact_ai":
        item.impact_ai = int(value) if value else None
    elif field == "effort_ai":
        item.effort_ai = value or None
    else:
        return Response(content="Campo no editable", status_code=422)
    await db.commit()
    return Response(status_code=204, headers={"HX-Refresh": "true"})


async def _render_relations(request: Request, db: AsyncSession, item: Item) -> Response:
    sub = await graph.subgraph(db, item.id)
    return templates.TemplateResponse(
        request, "partials/relationship_list.html", {"item": item, "subgraph": sub}
    )


@router.post("/ui/items/{item_id}/relationships")
async def ui_create_relationship(
    item_id: uuid.UUID,
    request: Request,
    relation: str = Form(...),
    target_query: str = Form(...),
    note: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.items import relationships

    item = await service.get_item(db, item_id)
    if item is None:
        return Response(status_code=404)
    guard = await _guard_row(db, user, item.project_id, write=True)
    if guard is not None:
        return guard
    try:
        target_id = await relationships.resolve_query(db, target_query)
        await relationships.create_relationship(db, item_id, target_id, relation, note or None)
        await db.commit()
    except relationships.RelationshipError as e:
        return Response(content=str(e), status_code=422)
    return await _render_relations(request, db, item)


@router.delete("/ui/items/{item_id}/relationships")
async def ui_delete_relationship(
    item_id: uuid.UUID,
    request: Request,
    source: uuid.UUID,
    target: uuid.UUID,
    relation: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.items import relationships

    item = await service.get_item(db, item_id)
    if item is None:
        return Response(status_code=404)
    guard = await _guard_row(db, user, item.project_id, write=True)
    if guard is not None:
        return guard
    await relationships.delete_relationship(db, source, target, relation)
    await db.commit()
    return await _render_relations(request, db, item)


@router.post("/ui/items/create")
async def ui_create_item(
    request: Request,
    title: str = Form(...),
    scope_id: str = Form(...),
    type: str = Form(...),
    status: str = Form("backlog"),
    summary_md: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    pid = await _project_id(db, user, request)
    scope = await db.get(Scope, uuid.UUID(scope_id))
    if scope is None or scope.project_id != pid:
        return Response(content="Scope does not belong to your project", status_code=422)
    item = Item(
        scope_id=scope.id, project_id=pid, title=title, type=type, status=status,
        summary_md=summary_md or None, origen="human", created_by=user.email,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    flash_success(request, message="Ítem creado")
    return RedirectResponse(f"/items/{item.id}", status_code=303)


# ---------- Hilos ----------

@router.get("/hilos", response_class=HTMLResponse)
async def hilos_page(
    request: Request,
    scope: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.threads.models import THREAD_STAGES
    from app.threads.service import list_threads

    pid = await _project_id(db, user, request)
    threads = await list_threads(db, scope_name=scope, project_id=pid)
    by_stage: dict[str, list] = {s: [] for s in THREAD_STAGES}
    for t in threads:
        by_stage.setdefault(t.stage, []).append(t)
    # contar artefactos e ítems por hilo (dos queries agrupadas, sin N+1)
    art_rows = (await db.execute(text(
        "SELECT thread_id, count(*) AS n FROM thread_artifacts GROUP BY thread_id"
    ))).mappings().all()
    item_rows = (await db.execute(text(
        "SELECT thread_id, count(*) AS n FROM items WHERE thread_id IS NOT NULL GROUP BY thread_id"
    ))).mappings().all()
    art_map = {str(r["thread_id"]): r["n"] for r in art_rows}
    item_map = {str(r["thread_id"]): r["n"] for r in item_rows}
    counts = {
        str(t.id): {"artifacts": art_map.get(str(t.id), 0), "items": item_map.get(str(t.id), 0)}
        for t in threads
    }
    scopes = list((await db.execute(
        select(Scope).where(Scope.archived.is_(False), Scope.project_id == pid).order_by(Scope.name)
    )).scalars().all())
    stages = [s for s in THREAD_STAGES if s != "descartado"]
    return templates.TemplateResponse(
        request, "hilos.html",
        {"user": user, "by_stage": by_stage, "stages": stages, "counts": counts,
         "scopes": scopes, "filters": {"scope": scope}},
    )


@router.get("/hilos/{thread_id}", response_class=HTMLResponse)
async def hilo_detail(
    thread_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.threads.models import ThreadArtifact, next_stage, prev_stage
    from app.threads.service import get_thread

    thread = await get_thread(db, thread_id)
    if thread is None:
        return Response(status_code=404, content="Hilo no encontrado")
    guard = await _guard_row(db, user, thread.project_id)
    if guard is not None:
        return guard
    arts = list((await db.execute(
        select(ThreadArtifact).where(ThreadArtifact.thread_id == thread_id)
        .order_by(ThreadArtifact.created_at)
    )).scalars().all())
    linked = list((await db.execute(
        select(Item).where(Item.thread_id == thread_id).order_by(Item.created_at)
    )).scalars().all())
    scope = await db.scalar(select(Scope).where(Scope.id == thread.scope_id))
    return templates.TemplateResponse(
        request, "hilo_detail.html",
        {"user": user, "thread": thread, "artifacts": arts, "linked": linked, "scope": scope,
         "next_stage": next_stage(thread.stage), "prev_stage": prev_stage(thread.stage)},
    )


@router.post("/ui/hilos/create")
async def ui_create_hilo(
    request: Request,
    title: str = Form(...),
    scope_name: str = Form(...),
    summary: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.threads.service import create_thread

    pid = await _project_id(db, user, request)
    t = await create_thread(db, scope_name, title, summary or None, project_id=pid)
    await db.commit()
    flash_success(request, message="Hilo creado")
    return RedirectResponse(f"/hilos/{t.id}", status_code=303)


@router.post("/ui/hilos/{thread_id}/advance")
async def ui_advance_hilo(
    thread_id: uuid.UUID,
    request: Request,
    artifact_content: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.threads.service import ThreadError, advance_stage, get_thread

    t = await get_thread(db, thread_id)
    if t is None:
        return Response(status_code=404)
    guard = await _guard_row(db, user, t.project_id, write=True)
    if guard is not None:
        return guard
    try:
        await advance_stage(db, t, artifact_content or None, user.id)
        await db.commit()
    except ThreadError as e:
        return Response(content=str(e), status_code=422)
    if t.stage == "hecho":
        flash_success(request, title=t.title, celebrate=True)
    return _refresh()


@router.post("/ui/hilos/{thread_id}/stage")
async def ui_set_hilo_stage(
    thread_id: uuid.UUID,
    request: Request,
    stage: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.threads.service import ThreadError, get_thread, set_stage

    t = await get_thread(db, thread_id)
    if t is None:
        return Response(status_code=404)
    guard = await _guard_row(db, user, t.project_id, write=True)
    if guard is not None:
        return guard
    try:
        await set_stage(db, t, stage)
        await db.commit()
    except ThreadError as e:
        return Response(content=str(e), status_code=422)
    if t.stage == "hecho":
        flash_success(request, title=t.title, celebrate=True)
    return _refresh()


@router.post("/ui/hilos/{thread_id}/elaborate", response_class=HTMLResponse)
async def ui_elaborate_hilo(
    thread_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.threads.service import ThreadError, elaborate_next_stage, get_thread

    t = await get_thread(db, thread_id)
    if t is None:
        return Response(status_code=404)
    guard = await _guard_row(db, user, t.project_id, write=True)
    if guard is not None:
        return guard
    try:
        draft = await elaborate_next_stage(db, t)
    except ThreadError as e:
        return HTMLResponse(
            f'<div class="text-sm text-red-600">{e}</div>', status_code=200
        )
    return templates.TemplateResponse(
        request, "partials/elaborate_draft.html", {"thread": t, "draft": draft}
    )


# ---------- Incidentes (contenedor de errores de Sentry) ----------

@router.get("/incidentes", response_class=HTMLResponse)
async def incidentes_page(
    request: Request,
    incluir_ignorados: bool = False,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.webhooks.models import SentryIssue

    pid = await _project_id(db, user, request)
    q = (
        select(SentryIssue)
        .where(SentryIssue.project_id == pid)
        .order_by(SentryIssue.last_seen.desc().nulls_last())
    )
    if not incluir_ignorados:
        q = q.where(SentryIssue.status != "ignored")
    issues = list((await db.execute(q.limit(200))).scalars().all())

    async def _count(st: str) -> int:
        return int(await db.scalar(
            select(func.count()).select_from(SentryIssue).where(
                SentryIssue.project_id == pid, SentryIssue.status == st
            )
        ) or 0)

    counts = {
        "new": await _count("new"),
        "linked": await _count("linked"),
        "ignored": await _count("ignored"),
    }
    return templates.TemplateResponse(
        request, "incidentes.html",
        {"user": user, "issues": issues, "counts": counts, "incluir_ignorados": incluir_ignorados},
    )


@router.post("/ui/incidentes/{issue_id}/promote")
async def ui_promote_issue(
    issue_id: uuid.UUID,
    request: Request,
    priority: str = Form("p1"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.webhooks import service as wservice
    from app.webhooks.models import SentryIssue

    issue = await db.get(SentryIssue, issue_id)
    if issue is None:
        return Response(status_code=404)
    guard = await _guard_row(db, user, issue.project_id, write=True)
    if guard is not None:
        return guard
    if priority not in ("p0", "p1", "p2", "p3"):
        priority = "p1"
    await wservice.promote_issue(db, issue, priority=priority, actor=user.email)
    await db.commit()
    flash_success(request, message="Incidente promovido al backlog")
    return _refresh()


@router.post("/ui/incidentes/{issue_id}/ignore")
async def ui_ignore_issue(
    issue_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.webhooks.models import SentryIssue

    issue = await db.get(SentryIssue, issue_id)
    if issue is None:
        return Response(status_code=404)
    guard = await _guard_row(db, user, issue.project_id, write=True)
    if guard is not None:
        return guard
    issue.status = "ignored"
    await db.commit()
    flash_success(request, message="Incidente ignorado")
    return _refresh()


@router.post("/ui/incidentes/backfill")
async def ui_backfill_sentry(
    org: str = Form(...),
    project: str = Form(...),
    token: str = Form(...),
    query: str = Form("is:unresolved"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    """Importa el histórico de errores desde la API de Sentry (solo owner)."""
    if user.account_role != "owner":
        return HTMLResponse(
            '<div class="text-sm text-red-600">No autorizado.</div>', status_code=403
        )
    from app.webhooks import service as wservice

    try:
        issues = await wservice.fetch_sentry_issues(token, org, project, query)
    except Exception as e:  # error de red / token / proyecto inválido
        return HTMLResponse(f'<div class="text-sm text-red-600">Error al consultar Sentry: {e}</div>')
    result = await wservice.backfill_issues(db, issues, project)
    await db.commit()
    return HTMLResponse(
        f'<div class="text-sm text-green-700">Importados {result["ingested"]} de '
        f'{result["total"]} incidentes. <a href="/incidentes" class="underline">Recargar</a></div>'
    )


# ---------- Ideas / Admin (sin cambios sustantivos) ----------

@router.get("/ideas", response_class=HTMLResponse)
async def ideas_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    pid = await _project_id(db, user, request)
    ideas = list((await db.execute(
        select(Item).where(Item.status == "idea", Item.project_id == pid)
        .order_by(Item.created_at.desc()).limit(50)
    )).scalars().all())
    scopes = list((await db.execute(
        select(Scope).where(Scope.archived.is_(False), Scope.project_id == pid).order_by(Scope.name)
    )).scalars().all())
    return templates.TemplateResponse(
        request, "ideas.html", {"user": user, "ideas": ideas, "scopes": scopes}
    )


@router.post("/ui/admin/tokens", response_class=HTMLResponse)
async def ui_create_token(
    request: Request,
    name: str = Form(...),
    scopes: str = Form("write"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    if not user.is_superadmin:
        return HTMLResponse(
            '<div class="text-sm text-red-600">No autorizado.</div>', status_code=403
        )
    from app.auth.service import create_api_token

    if scopes not in ("read", "write"):
        scopes = "write"
    _tok, raw = await create_api_token(db, name, scopes, user.id)
    return templates.TemplateResponse(
        request, "partials/token_created.html", {"raw": raw, "name": name, "scopes": scopes}
    )


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    if not user.is_superadmin:
        return RedirectResponse("/", status_code=303)

    users = list((await db.execute(select(User).order_by(User.created_at))).scalars().all())
    tokens = list((await db.execute(
        select(ApiToken).where(ApiToken.revoked_at.is_(None)).order_by(ApiToken.created_at.desc())
    )).scalars().all())
    scopes = list((await db.execute(
        select(Scope).order_by(Scope.display_order, Scope.name)
    )).scalars().all())
    runs = list((await db.execute(
        select(AgentRun).order_by(AgentRun.created_at.desc()).limit(20)
    )).scalars().all())

    return templates.TemplateResponse(
        request,
        "admin.html",
        {"user": user, "users": users, "tokens": tokens, "scopes": scopes, "runs": runs},
    )
