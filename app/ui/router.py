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
from app.scopes.models import Scope
from app.templates_config import templates

router = APIRouter(tags=["ui"])

_OPEN = ["idea", "backlog", "spec", "en-curso", "bloqueado", "en-revision"]
_PRIORITY_RANK = {"p0": 0, "p1": 1, "p2": 2, "p3": 3, None: 9}


def _recent_touch(item: Item) -> bool:
    if item.last_touched_at is None:
        return False
    return item.last_touched_at > datetime.now(timezone.utc) - timedelta(hours=24)


# ---------- Tablero ----------

@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    counts_q = await db.execute(select(Item.status, func.count().label("n")).group_by(Item.status))
    counts = {row.status: row.n for row in counts_q}

    blocked_ids = await graph.graph_blocked_ids(db)

    recent_q = await db.execute(select(Item).order_by(Item.created_at.desc()).limit(10))
    recent = recent_q.scalars().all()

    qw_q = await db.execute(
        select(Item)
        .where(
            Item.impact_ai >= 4,
            Item.effort_ai.in_(["XS", "S"]),
            Item.status.not_in(["hecho", "descartado"]),
        )
        .order_by(Item.impact_ai.desc())
        .limit(5)
    )
    quick_wins = qw_q.scalars().all()

    cost_q = await db.scalar(select(func.sum(AgentRun.cost_usd)).where(AgentRun.status == "ok"))
    monthly_cost = float(cost_q or 0)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "counts": counts,
            "blocked_count": len(blocked_ids),
            "recent": recent,
            "recent_touch": {str(i.id): _recent_touch(i) for i in recent},
            "quick_wins": quick_wins,
            "monthly_cost": monthly_cost,
        },
    )


# ---------- Backlog ----------

@router.get("/backlog", response_class=HTMLResponse)
async def backlog(
    request: Request,
    scope: str | None = None,
    status: str | None = None,
    item_type: str | None = None,
    origen: str | None = None,
    stale: bool | None = None,
    graph_blocked: bool | None = None,
    order: str = "prioridad",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    q = select(Item)
    if scope:
        scope_row = await db.scalar(select(Scope).where(Scope.name == scope))
        if scope_row:
            q = q.where(Item.scope_id == scope_row.id)
    if status:
        q = q.where(Item.status == status)
    if item_type:
        q = q.where(Item.type == item_type)
    if origen:
        q = q.where(Item.origen == origen)
    if stale is not None:
        q = q.where(Item.stale_risk == stale)
    q = q.limit(300)
    items = list((await db.execute(q)).scalars().all())

    blocked_ids = await graph.graph_blocked_ids(db)
    unblocker_ids = await graph.unblocker_ids(db)

    if graph_blocked:
        items = [i for i in items if str(i.id) in blocked_ids]

    items = _order_items(items, order, await _topo_order_ids(db, items) if order == "topologico" else None)

    scopes = list((await db.execute(
        select(Scope).where(Scope.archived.is_(False)).order_by(Scope.name)
    )).scalars().all())
    scope_map = {s.id: s.name for s in scopes}

    ctx = {
        "user": user,
        "items": items,
        "scopes": scopes,
        "scope_map": scope_map,
        "blocked_ids": blocked_ids,
        "unblocker_ids": unblocker_ids,
        "recent_touch": {str(i.id): _recent_touch(i) for i in items},
        "filters": {
            "scope": scope, "status": status, "type": item_type, "origen": origen,
            "stale": stale, "graph_blocked": graph_blocked, "order": order,
        },
    }
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "partials/items_table.html", ctx)
    return templates.TemplateResponse(request, "backlog.html", ctx)


def _order_items(items: list[Item], order: str, topo_rank: dict[str, int] | None) -> list[Item]:
    if order == "impacto":
        return sorted(items, key=lambda i: (-(i.impact_ai or 0), i.effort_ai or "ZZ"))
    if order == "prioridad":
        return sorted(items, key=lambda i: (_PRIORITY_RANK.get(i.priority, 9), -(i.impact_ai or 0)))
    if order == "topologico" and topo_rank is not None:
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

    scope = await db.scalar(select(Scope).where(Scope.id == item.scope_id))
    blockers = await graph.blockers_of(db, item.id)
    sub = await graph.subgraph(db, item.id)
    scopes = list((await db.execute(
        select(Scope).where(Scope.archived.is_(False)).order_by(Scope.name)
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
    q = select(Item).where(Item.status.in_(_OPEN))
    if scope:
        scope_row = await db.scalar(select(Scope).where(Scope.name == scope))
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
        select(Scope).where(Scope.archived.is_(False)).order_by(Scope.name)
    )).scalars().all())

    return templates.TemplateResponse(
        request,
        "prioridad.html",
        {
            "user": user, "items": ranked, "matrix": matrix, "efforts": efforts,
            "impacts": [5, 4, 3, 2, 1], "unestimated": unestimated,
            "scopes": scopes, "filters": {"scope": scope},
        },
    )


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
    try:
        await service.apply_transition(db, item, status, user.email)
        await db.commit()
    except service.TransitionError as e:
        return Response(content=str(e), status_code=422)
    return _refresh()


@router.post("/ui/items/{item_id}/close")
async def ui_close(
    item_id: uuid.UUID,
    status: str = Form(...),
    reason: str = Form(""),
    commit_sha: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    item = await service.get_item(db, item_id)
    if item is None:
        return Response(status_code=404)
    try:
        await service.close_item(db, item, status, reason or None, user.email, commit_sha or None)
        await db.commit()
    except service.TransitionError as e:
        return Response(content=str(e), status_code=422)
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
    await relationships.delete_relationship(db, source, target, relation)
    await db.commit()
    return await _render_relations(request, db, item)


@router.post("/ui/items/create")
async def ui_create_item(
    title: str = Form(...),
    scope_id: str = Form(...),
    type: str = Form(...),
    status: str = Form("backlog"),
    summary_md: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    item = Item(
        scope_id=uuid.UUID(scope_id), title=title, type=type, status=status,
        summary_md=summary_md or None, origen="humano", created_by=user.email,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
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

    threads = await list_threads(db, scope_name=scope)
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
        select(Scope).where(Scope.archived.is_(False)).order_by(Scope.name)
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
    title: str = Form(...),
    scope_name: str = Form(...),
    summary: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.threads.service import create_thread

    t = await create_thread(db, scope_name, title, summary or None)
    await db.commit()
    return RedirectResponse(f"/hilos/{t.id}", status_code=303)


@router.post("/ui/hilos/{thread_id}/advance")
async def ui_advance_hilo(
    thread_id: uuid.UUID,
    artifact_content: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.threads.service import ThreadError, advance_stage, get_thread

    t = await get_thread(db, thread_id)
    if t is None:
        return Response(status_code=404)
    try:
        await advance_stage(db, t, artifact_content or None, user.id)
        await db.commit()
    except ThreadError as e:
        return Response(content=str(e), status_code=422)
    return _refresh()


@router.post("/ui/hilos/{thread_id}/stage")
async def ui_set_hilo_stage(
    thread_id: uuid.UUID,
    stage: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.threads.service import ThreadError, get_thread, set_stage

    t = await get_thread(db, thread_id)
    if t is None:
        return Response(status_code=404)
    try:
        await set_stage(db, t, stage)
        await db.commit()
    except ThreadError as e:
        return Response(content=str(e), status_code=422)
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

    q = select(SentryIssue).order_by(SentryIssue.last_seen.desc().nulls_last())
    if not incluir_ignorados:
        q = q.where(SentryIssue.status != "ignored")
    issues = list((await db.execute(q.limit(200))).scalars().all())
    counts = {
        "new": await db.scalar(
            select(func.count()).select_from(SentryIssue).where(SentryIssue.status == "new")
        ),
        "linked": await db.scalar(
            select(func.count()).select_from(SentryIssue).where(SentryIssue.status == "linked")
        ),
        "ignored": await db.scalar(
            select(func.count()).select_from(SentryIssue).where(SentryIssue.status == "ignored")
        ),
    }
    return templates.TemplateResponse(
        request, "incidentes.html",
        {"user": user, "issues": issues, "counts": counts, "incluir_ignorados": incluir_ignorados},
    )


@router.post("/ui/incidentes/{issue_id}/promote")
async def ui_promote_issue(
    issue_id: uuid.UUID,
    priority: str = Form("p1"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.webhooks import service as wservice
    from app.webhooks.models import SentryIssue

    issue = await db.get(SentryIssue, issue_id)
    if issue is None:
        return Response(status_code=404)
    if priority not in ("p0", "p1", "p2", "p3"):
        priority = "p1"
    await wservice.promote_issue(db, issue, priority=priority, actor=user.email)
    await db.commit()
    return _refresh()


@router.post("/ui/incidentes/{issue_id}/ignore")
async def ui_ignore_issue(
    issue_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.webhooks.models import SentryIssue

    issue = await db.get(SentryIssue, issue_id)
    if issue is None:
        return Response(status_code=404)
    issue.status = "ignored"
    await db.commit()
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
    """Importa el histórico de errores desde la API de Sentry (solo admin)."""
    if user.role != "admin":
        return Response(status_code=403)
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
    ideas = list((await db.execute(
        select(Item).where(Item.status == "idea").order_by(Item.created_at.desc()).limit(50)
    )).scalars().all())
    scopes = list((await db.execute(
        select(Scope).where(Scope.archived.is_(False)).order_by(Scope.name)
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
    if user.role != "admin":
        return Response(status_code=403)
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
    if user.role != "admin":
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
