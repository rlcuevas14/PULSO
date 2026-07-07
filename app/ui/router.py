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
from app.i18n import resolve_lang
from app.i18n import t as _t
from app.items import graph, service
from app.items.lifecycle import allowed_targets, non_terminal_targets
from app.items.models import Item, ItemEvent
from app.jobs.models import AgentRun
from app.projects.access import resolve_current_project, user_role_on_project
from app.scopes.models import Scope
from app.templates_config import templates
from app.ui.flash import flash_success

router = APIRouter(tags=["ui"])

_OPEN = ["idea", "backlog", "spec", "in-progress", "blocked", "in-review"]


@router.get("/ui/lang/{code}")
async def ui_set_lang(code: str, request: Request, next: str = "/"):
    """Language switch — no auth dependency so it also works on the login page."""
    from app.i18n import SUPPORTED

    if code not in SUPPORTED:
        return Response(status_code=404)
    request.session["lang"] = code
    # Open-redirect guard: only same-site relative paths.
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    return RedirectResponse(target, status_code=303)
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

    from datetime import datetime as _dt_cls
    week_start = _dt_cls.now(timezone.utc)
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = week_start - timedelta(days=week_start.weekday())
    closed_this_week = int(await db.scalar(
        select(func.count()).select_from(Item).where(
            Item.project_id == pid,
            Item.status.in_(["done", "discarded"]),
            Item.closed_at >= week_start,
        )
    ) or 0)

    cards = {
        "open": sum(counts.get(s, 0) for s in ("backlog", "spec", "in-progress", "blocked", "in-review")),
        "in_progress": counts.get("in-progress", 0),
        "blocked": len(blocked_ids),
        "quick_wins": quick_wins_n,
        "threads_active": threads_active,
        "incidents_new": incidents_new,
        "ideas": counts.get("idea", 0),
        "closed_this_week": closed_this_week,
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


async def _backlog_context(
    request: Request,
    db: AsyncSession,
    user: User,
    *,
    scope: str | None = None,
    status: str | None = None,
    item_type: str | None = None,
    origen: str | None = None,
    stale: bool | None = None,
    graph_blocked: bool | None = None,
    order: str = "priority",
    show: str = "open",
    q: str | None = None,
    priority: str | None = None,
    effort: str | None = None,
    quickwins: bool = False,
    urgent: bool = False,
    agent_ready: bool = False,
    view: str = "list",
    group: str = "",
) -> dict:
    """Construye el contexto del backlog (items filtrados + estado derivado + board/group).

    Compartido por GET /backlog y POST /ui/items/{id}/board-move, para que un
    movimiento por drag&drop re-renderice el tablero con los MISMOS filtros activos
    y recalcule bloqueos/ready/contadores (mover una tarjeta puede desbloquear otras).
    """
    from sqlalchemy import case as sa_case

    from app.items.search import search_items as _fts

    pid = await _project_id(db, user, request)
    q_base = select(Item).where(Item.project_id == pid)

    # El tablero nunca muestra terminales: un filtro de estado terminal se ignora (spec §1.5).
    if view == "board" and status in ("done", "discarded"):
        status = None

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

    # FTS: filtra en SQL (antes del LIMIT) para no perder matches fuera del top-300.
    fts_rank: dict[str, int] | None = None
    if q:
        fts_rows = await _fts(db, q, project_id=pid, limit=300)
        if fts_rows:
            q_base = q_base.where(Item.id.in_([uuid.UUID(r["id"]) for r in fts_rows]))
            fts_rank = {r["id"]: n for n, r in enumerate(fts_rows)}
        else:
            q_base = q_base.where(Item.id == uuid.UUID(int=0))

    # SQL ordering — topological stays in Python (alias españoles: URLs viejas)
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
    elif order in ("impact", "impacto"):
        q_base = q_base.order_by(Item.impact_ai.desc().nullslast())
    elif order in ("recent", "reciente"):
        q_base = q_base.order_by(Item.created_at.desc())

    q_base = q_base.limit(300)
    items = list((await db.execute(q_base)).scalars().all())

    # Con búsqueda activa y orden default, el orden es relevance (rank del FTS).
    if fts_rank is not None and order in ("priority", "prioridad"):
        items.sort(key=lambda i: fts_rank.get(str(i.id), 1_000_000))

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

    # can_write: gate del drag&drop en el tablero (los viewers ven pero no mueven).
    role = await user_role_on_project(db, user, pid)
    can_write = role is not None and role != "viewer"

    ctx: dict = {
        "user": user,
        "items": items,
        "scopes": scopes,
        "scope_map": scope_map,
        "blocked_ids": blocked_ids,
        "unblocker_ids": unblocker_ids,
        "ready_ids": ready_ids,
        "can_write": can_write,
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

    # Group-by context — labels localizados (los headers los pinta items_grouped.html tal cual)
    if group and group != "none" and view != "board":
        lang = resolve_lang(request)
        grouped: dict[str, list] = {}
        for item in items:
            if group == "scope":
                key = scope_map.get(item.scope_id) or _t("backlog.group_no_scope", lang)
            elif group == "type":
                key = _t(f"type.{item.type}", lang) if item.type else _t("backlog.group_no_scope", lang)
            elif group == "priority":
                key = item.priority or _t("backlog.group_no_priority", lang)
            elif group == "status":
                key = item.status
            else:
                key = "—"
            grouped.setdefault(key, []).append(item)
        if group == "status":
            # Orden de funnel, no alfabético; label localizado al construir la lista
            _sidx = {s: n for n, s in enumerate([*_BOARD_STATUSES, "done", "discarded"])}
            ctx["groups"] = [
                (_t(f"status.{k}", lang), v)
                for k, v in sorted(grouped.items(), key=lambda kv: _sidx.get(kv[0], 99))
            ]
        else:
            ctx["groups"] = sorted(grouped.items())

    return ctx


@router.get("/backlog", response_class=HTMLResponse)
async def backlog(
    request: Request,
    scope: str | None = None,
    status: str | None = None,
    item_type: str | None = None,
    origen: str | None = None,
    stale: bool | None = None,
    graph_blocked: bool | None = None,
    order: str = "priority",
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
    ctx = await _backlog_context(
        request, db, user, scope=scope, status=status, item_type=item_type,
        origen=origen, stale=stale, graph_blocked=graph_blocked, order=order,
        show=show, q=q, priority=priority, effort=effort, quickwins=quickwins,
        urgent=urgent, agent_ready=agent_ready, view=view, group=group,
    )
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


@router.post("/ui/items/{item_id}/board-move", response_class=HTMLResponse)
async def ui_board_move(
    item_id: uuid.UUID,
    request: Request,
    status: str = Form(...),
    # Passthrough de filtros para re-renderizar el tablero tal como estaba.
    scope: str | None = Form(None),
    item_type: str | None = Form(None),
    origen: str | None = Form(None),
    stale: bool | None = Form(None),
    graph_blocked: bool | None = Form(None),
    order: str = Form("priority"),
    q: str | None = Form(None),
    priority: str | None = Form(None),
    effort: str | None = Form(None),
    quickwins: bool = Form(False),
    urgent: bool = Form(False),
    agent_ready: bool = Form(False),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    """Drag&drop del tablero: aplica la transición y devuelve el tablero re-renderizado.

    Movimiento inválido (matriz de lifecycle) → devuelve el tablero SIN cambios (la
    tarjeta vuelve a su columna) + un toast de error vía HX-Trigger. Nunca 422: el
    swap parcial siempre repinta un tablero coherente.
    """
    import json as _json

    item = await service.get_item(db, item_id)
    if item is None:
        return Response(status_code=404)
    guard = await _guard_row(db, user, item.project_id, write=True)
    if guard is not None:
        return guard

    invalid: str | None = None
    try:
        await service.apply_transition(db, item, status, user.email)
        await db.commit()
    except service.TransitionError as e:
        # apply_transition valida ANTES de mutar, así que la sesión sigue limpia
        # (nada que revertir); un rollback aquí expiraría los objetos del re-render.
        invalid = str(e)

    ctx = await _backlog_context(
        request, db, user, scope=scope, item_type=item_type, origen=origen,
        stale=stale, graph_blocked=graph_blocked, order=order, q=q,
        priority=priority, effort=effort, quickwins=quickwins, urgent=urgent,
        agent_ready=agent_ready, view="board", show="open", group="",
    )
    resp = templates.TemplateResponse(request, "partials/items_board.html", ctx)
    if invalid:
        resp.headers["HX-Trigger"] = _json.dumps(
            {"pulso:toast": {"message": invalid, "kind": "error"}}
        )
    return resp


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
        flash_success(request, message=_t("flash.item_discarded", resolve_lang(request)))
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


@router.get("/ui/items/{item_id}/close-modal", response_class=HTMLResponse)
async def ui_close_modal(
    item_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    item = await service.get_item(db, item_id)
    if item is None:
        return Response(status_code=404)
    guard = await _guard_row(db, user, item.project_id)
    if guard is not None:
        return guard
    return templates.TemplateResponse(request, "partials/_close_modal.html", {"item": item})


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
    flash_success(request, message=_t("flash.item_created", resolve_lang(request)))
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
    flash_success(request, message=_t("flash.thread_created", resolve_lang(request)))
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
    flash_success(request, message=_t("flash.incident_promoted", resolve_lang(request)))
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
    flash_success(request, message=_t("flash.incident_ignored", resolve_lang(request)))
    return _refresh()


@router.post("/ui/incidentes/backfill")
async def ui_backfill_sentry(
    request: Request,
    org: str = Form(...),
    project: str = Form(...),
    token: str = Form(...),
    query: str = Form("is:unresolved"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    """Importa el histórico de errores desde la API de Sentry (solo owner)."""
    if user.account_role != "owner":
        msg = _t("admin.unauthorized", resolve_lang(request))
        return HTMLResponse(f'<div class="text-sm text-red-600">{msg}</div>', status_code=403)
    from app.webhooks import service as wservice

    try:
        issues = await wservice.fetch_sentry_issues(token, org, project, query)
    except Exception as e:  # error de red / token / proyecto inválido
        msg = _t("incidents.backfill_error", resolve_lang(request), error=e)
        return HTMLResponse(f'<div class="text-sm text-red-600">{msg}</div>')
    result = await wservice.backfill_issues(db, issues, project)
    await db.commit()
    lang = resolve_lang(request)
    return HTMLResponse(
        f'<div class="text-sm text-green-700">'
        f'{_t("incidents.backfill_ok", lang, n=result["ingested"], total=result["total"])} '
        f'<a href="/incidentes" class="underline">{_t("incidents.reload", lang)}</a></div>'
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


# ---------- Archive (Registro) ----------

# Ventana por página: 12 semanas con contenido (spec §2.1). El corte es en límite de
# semana para que "Cargar más" nunca duplique headers de semana.
_WEEKS_PER_PAGE = 12
# ponytail: cap de fetch por página; >500 cerrados en 12 semanas es irreal para esta
# herramienta — si llega a doler, paginar dentro de la semana.
_FETCH_CAP = 500


def _iso_week_label(dt: datetime | None) -> str:
    """Return 'YYYY-Www' key for grouping; '' for None (→ 'Sin fecha')."""
    if dt is None:
        return ""
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _week_range_label(iso_week_key: str, lang: str) -> str:
    """Localized 'Week of Mon D – Sun D, MMM YYYY' from 'YYYY-Www' (month.* catalog keys)."""
    import datetime as _dt
    year, week = int(iso_week_key[:4]), int(iso_week_key[6:])
    mon = _dt.datetime.fromisocalendar(year, week, 1)
    sun = mon + _dt.timedelta(days=6)
    if mon.month == sun.month:
        return _t("registro.week_same_month", lang, d1=mon.day, d2=sun.day,
                  month=_t(f"month.{sun.month}", lang), year=sun.year)
    return _t("registro.week_cross_month", lang, d1=mon.day, m1=_t(f"month.{mon.month}", lang),
              d2=sun.day, m2=_t(f"month.{sun.month}", lang), year=sun.year)


@router.get("/registro", response_class=HTMLResponse)
async def registro_page(
    request: Request,
    q: str | None = None,
    scope: str | None = None,
    item_type: str | None = None,
    before: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    import datetime as _dt
    from itertools import groupby

    from app.items.search import search_items

    lang = resolve_lang(request)
    project = await resolve_current_project(db, user, request)
    pid = project.id if project else uuid.UUID(int=0)

    base = select(Item).where(
        Item.project_id == pid,
        Item.status.in_(["done", "discarded"]),
    )
    if q:
        ids = [uuid.UUID(r["id"]) for r in await search_items(db, q, project_id=pid, limit=300)]
        base = base.where(Item.id.in_(ids)) if ids else base.where(Item.id == uuid.UUID(int=0))
    if scope:
        scope_row = await db.scalar(select(Scope).where(Scope.name == scope, Scope.project_id == pid))
        if scope_row:
            base = base.where(Item.scope_id == scope_row.id)
    if item_type:
        base = base.where(Item.type == item_type)

    before_dt: datetime | None = None
    if before:
        try:
            before_dt = _dt.datetime.fromisoformat(before)
            if before_dt.tzinfo is None:
                before_dt = before_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            before_dt = None

    dated = base.where(Item.closed_at.isnot(None))
    if before_dt:
        dated = dated.where(Item.closed_at < before_dt)
    dated = dated.order_by(Item.closed_at.desc()).limit(_FETCH_CAP)
    items = list((await db.execute(dated)).scalars().all())

    # Agrupar por semana ISO y cortar la página en límite de semana (12 semanas).
    groups_all = [
        (key, list(grp)) for key, grp in groupby(items, key=lambda i: _iso_week_label(i.closed_at))
    ]
    page = groups_all[:_WEEKS_PER_PAGE]
    has_more_dated = len(groups_all) > _WEEKS_PER_PAGE or len(items) == _FETCH_CAP

    next_before: str | None = None
    if has_more_dated and page:
        # Lunes 00:00 UTC de la semana más vieja incluida: todo lo estrictamente
        # anterior pertenece a semanas previas → sin headers duplicados.
        last_key = page[-1][0]
        y, w = int(last_key[:4]), int(last_key[6:])
        next_before = _dt.datetime.fromisocalendar(y, w, 1).replace(
            tzinfo=timezone.utc
        ).isoformat()

    week_groups: list[tuple[str, str, list[Item]]] = [
        (key, _week_range_label(key, lang), grp) for key, grp in page
    ]

    # "Sin fecha" (terminales legacy sin closed_at): solo en la última página.
    if not has_more_dated:
        undated = list((await db.execute(
            base.where(Item.closed_at.is_(None)).order_by(Item.created_at.desc())
        )).scalars().all())
        if undated:
            week_groups.append(("", _t("registro.no_date", lang), undated))

    page_items = [i for _, _, grp in week_groups for i in grp]

    # Batch-fetch close events (avoids N+1)
    item_ids = [i.id for i in page_items]
    events = list((await db.execute(
        select(ItemEvent).where(ItemEvent.item_id.in_(item_ids), ItemEvent.action == "closed")
        .order_by(ItemEvent.created_at.desc())
    )).scalars().all())
    # Most-recent close event per item
    close_event: dict[str, ItemEvent] = {}
    for ev in events:
        key = str(ev.item_id)
        if key not in close_event:
            close_event[key] = ev

    # Scope name map
    scope_ids = {i.scope_id for i in page_items if i.scope_id}
    scope_rows = list((await db.execute(select(Scope).where(Scope.id.in_(scope_ids)))).scalars().all())
    scope_map = {str(s.id): s.name for s in scope_rows}

    scopes = list((await db.execute(
        select(Scope).where(Scope.project_id == pid).order_by(Scope.name)
    )).scalars().all())

    ctx = {
        "user": user,
        "project": project,
        "week_groups": week_groups,
        "close_event": close_event,
        "scope_map": scope_map,
        "scopes": scopes,
        "filters": {"q": q, "scope": scope, "item_type": item_type},
        "next_before": next_before,
        "is_load_more": before_dt is not None,
    }
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "partials/registro_rows.html", ctx)
    return templates.TemplateResponse(request, "registro.html", ctx)


@router.get("/ui/registro/summary", response_class=HTMLResponse)
async def ui_registro_summary(
    request: Request,
    week: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.ai.llm import LLMUnavailable, summarize_closed

    pid = await _project_id(db, user, request)

    # Parse week key YYYY-Www → date range
    try:
        import datetime as _dt
        year, wnum = int(week[:4]), int(week[6:])
        mon = _dt.datetime.fromisocalendar(year, wnum, 1).replace(tzinfo=timezone.utc)
        sun = mon + _dt.timedelta(days=6, hours=23, minutes=59, seconds=59)
    except (ValueError, IndexError):
        return HTMLResponse(
            f'<p class="text-sm text-error">{_t("registro.invalid_week", resolve_lang(request))}</p>',
            status_code=400,
        )

    items = list((await db.execute(
        select(Item).where(
            Item.project_id == pid,
            Item.status.in_(["done", "discarded"]),
            Item.closed_at >= mon,
            Item.closed_at <= sun,
        )
    )).scalars().all())

    item_ids = [i.id for i in items]
    events = list((await db.execute(
        select(ItemEvent).where(ItemEvent.item_id.in_(item_ids), ItemEvent.action == "closed")
        .order_by(ItemEvent.created_at.desc())
    )).scalars().all())
    close_event = {str(ev.item_id): ev for ev in reversed(events)}

    items_data = [
        {
            "title": i.title,
            "type": i.type,
            "status": i.status,
            "reason": (close_event[str(i.id)].payload or {}).get("reason")
            if str(i.id) in close_event else None,
        }
        for i in items
    ]

    try:
        summary_md = await summarize_closed(items_data, lang=resolve_lang(request))
    except LLMUnavailable:
        summary_md = None

    return templates.TemplateResponse(
        request, "partials/registro_summary.html",
        {"summary_md": summary_md, "week": week},
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
        msg = _t("admin.unauthorized", resolve_lang(request))
        return HTMLResponse(f'<div class="text-sm text-red-600">{msg}</div>', status_code=403)
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
