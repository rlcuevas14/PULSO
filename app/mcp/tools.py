"""Implementación de las tools MCP de Pulso.

Cada tool reutiliza la lógica de servicio (lifecycle, grafo, relaciones) para no
divergir de la UI/REST. Las business-errors se propagan como ToolError → isError.
"""

import uuid
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import ApiToken, User
from app.items import graph, relationships, service
from app.items.lifecycle import valid_transition
from app.items.models import Item
from app.scopes.models import Scope

_OPEN = ["idea", "backlog", "spec", "en-curso", "bloqueado", "en-revision"]


class ToolError(Exception):
    """Error de negocio de una tool (se devuelve como isError, no error JSON-RPC)."""


async def actor_for(db: AsyncSession, token: ApiToken) -> str:
    """Resuelve el actor de una escritura: email del creador del token, o token:<name>."""
    user = (await db.execute(select(User).where(User.id == token.created_by))).scalar_one_or_none()
    return user.email if user else f"token:{token.name}"


async def _resolve_scope(db: AsyncSession, name: str, create: bool = False) -> Scope:
    # Match case-insensitive para no crear duplicados ("Currículo" == "curriculo").
    scope = (await db.execute(
        select(Scope).where(func.lower(Scope.name) == name.strip().lower())
    )).scalar_one_or_none()
    if scope is None:
        if not create:
            raise ToolError(f"Scope «{name}» no existe. Usa pulso_scopes para ver los disponibles.")
        scope = Scope(name=name.strip(), source_repo="mcp")
        db.add(scope)
        await db.flush()
    return scope


async def _scope_map(db: AsyncSession) -> dict[str, str]:
    rows = (await db.execute(select(Scope.id, Scope.name))).all()
    return {str(r[0]): r[1] for r in rows}


async def _resolve_item(db: AsyncSession, ref: str) -> Item:
    """Acepta un UUID o una query de texto (resuelta por full-text con abort por ambigüedad)."""
    try:
        item_id = uuid.UUID(ref)
    except (ValueError, AttributeError):
        try:
            item_id = await relationships.resolve_query(db, ref)
        except relationships.RelationshipError as e:
            raise ToolError(str(e)) from e
    item = await service.get_item(db, item_id)
    if item is None:
        raise ToolError(f"Ítem no encontrado: {ref}")
    return item


def _item_brief(i: Item, scope_map: dict[str, str] | None = None) -> dict[str, Any]:
    brief = {
        "id": str(i.id), "title": i.title, "type": i.type, "status": i.status,
        "priority": i.priority, "impact_ai": i.impact_ai, "effort_ai": i.effort_ai,
        "scope_id": str(i.scope_id), "origen": i.origen,
    }
    if scope_map is not None:
        brief["scope"] = scope_map.get(str(i.scope_id))
    if i.thread_id is not None:
        brief["thread_id"] = str(i.thread_id)
    return brief


# ---------- Tools de lectura ----------

async def pulso_contexto(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    scope_name = args.get("scope")
    work = args.get("work_description")
    scope = None
    if scope_name:
        scope = (await db.execute(select(Scope).where(Scope.name == scope_name))).scalar_one_or_none()

    base = select(Item).where(Item.status.in_(_OPEN))
    if scope:
        base = base.where(Item.scope_id == scope.id)

    # Quickwins (con fallback a prioridad humana si no hay impacto IA — degradación sin F2).
    qw = (await db.execute(
        base.where(Item.impact_ai >= 4, Item.effort_ai.in_(["XS", "S"]))
        .order_by(Item.impact_ai.desc()).limit(5)
    )).scalars().all()
    if not qw:
        qw = (await db.execute(
            base.where(Item.priority.in_(["p0", "p1"])).order_by(Item.priority).limit(5)
        )).scalars().all()

    blockers = (await db.execute(base.where(Item.status == "bloqueado").limit(10))).scalars().all()

    # Bugs de Sentry sin ítem asociado (la tabla existe; consulta defensiva).
    try:
        sentry = (await db.execute(text(
            "SELECT id, title, level FROM sentry_issues WHERE item_id IS NULL "
            "ORDER BY last_seen DESC NULLS LAST LIMIT 5"
        ))).mappings().all()
        sentry_bugs = [{"id": str(r["id"]), "title": r["title"], "level": r["level"]} for r in sentry]
    except Exception:
        sentry_bugs = []

    # Hilos en desarrollo.
    try:
        threads = (await db.execute(text(
            "SELECT id, title FROM threads WHERE stage = 'en-desarrollo' LIMIT 5"
        ))).mappings().all()
        active_threads = [{"id": str(r["id"]), "title": r["title"]} for r in threads]
    except Exception:
        active_threads = []

    smap = await _scope_map(db)
    result: dict[str, Any] = {
        "local": {
            "quickwins": [_item_brief(i, smap) for i in qw],
            "blockers": [_item_brief(i, smap) for i in blockers],
            "sentry_sin_item": sentry_bugs,
            "hilos_en_desarrollo": active_threads,
        },
        "neighborhood": await graph.neighborhood(db, scope.id) if scope else [],
    }

    # Capa semántica: solo si hay work_description y embeddings disponibles.
    if work and await service.touch_embedding_available(db):
        from app.ai import llm
        vec = await llm.embed_text(work)
        if vec:
            rows = (await db.execute(text("""
                SELECT id, title, status FROM items
                WHERE embedding IS NOT NULL AND status NOT IN ('hecho','descartado')
                ORDER BY embedding <=> CAST(:vec AS vector) LIMIT 5
            """), {"vec": str(vec)})).mappings().all()
            result["semantic"] = [{"id": str(r["id"]), "title": r["title"]} for r in rows]
        else:
            result["semantic"] = None
            result["semantic_status"] = "sin-embedding-de-consulta"
    else:
        result["semantic"] = None
        result["semantic_status"] = "pendiente-f2" if work else "no-solicitada"

    return result


async def pulso_buscar(db: AsyncSession, token: ApiToken, args: dict) -> list[dict]:
    q = args["q"]
    rows = (await db.execute(text("""
        SELECT i.id, i.title, i.summary_md, i.type, i.status, i.scope_id, s.name AS scope,
               i.effort_ai, i.impact_ai,
               ts_rank(i.search_vector, plainto_tsquery('spanish', :q)) AS rank
        FROM items i JOIN scopes s ON s.id = i.scope_id
        WHERE i.search_vector @@ plainto_tsquery('spanish', :q)
        ORDER BY rank DESC, i.id LIMIT :limit
    """), {"q": q, "limit": int(args.get("limit", 10))})).mappings().all()
    return [
        {"id": str(r["id"]), "title": r["title"], "summary_md": r["summary_md"],
         "type": r["type"], "status": r["status"], "scope_id": str(r["scope_id"]),
         "scope": r["scope"], "effort_ai": r["effort_ai"], "impact_ai": r["impact_ai"]}
        for r in rows
    ]


async def pulso_listar(db: AsyncSession, token: ApiToken, args: dict) -> list[dict]:
    q = select(Item)
    if args.get("scope"):
        scope = (await db.execute(select(Scope).where(Scope.name == args["scope"]))).scalar_one_or_none()
        if scope:
            q = q.where(Item.scope_id == scope.id)
    statuses = args.get("status")
    if statuses:
        q = q.where(Item.status.in_(statuses))
    if args.get("tipo"):
        q = q.where(Item.type == args["tipo"])
    if args.get("quickwins"):
        q = q.where(Item.impact_ai >= 4, Item.effort_ai.in_(["XS", "S"]))
    items = list((await db.execute(q.limit(int(args.get("limit", 20))))).scalars().all())

    order = args.get("order", "impacto")
    if order == "topologico":
        ids = [str(i.id) for i in items]
        rels = (await db.execute(text(
            "SELECT source_id, target_id, relation FROM item_relationships "
            "WHERE source_id = ANY(:ids) AND target_id = ANY(:ids)"
        ), {"ids": ids})).mappings().all()
        edges = [(str(r["source_id"]), str(r["target_id"]), r["relation"]) for r in rels]
        ranked = graph.topological_order(ids, edges, {str(i.id): (i.impact_ai or 0) for i in items})
        rank = {x: n for n, x in enumerate(ranked["order"])}
        items.sort(key=lambda i: rank.get(str(i.id), 10**6))
    elif order == "prioridad":
        pr = {"p0": 0, "p1": 1, "p2": 2, "p3": 3, None: 9}
        items.sort(key=lambda i: (pr.get(i.priority, 9), -(i.impact_ai or 0)))
    else:
        items.sort(key=lambda i: -(i.impact_ai or 0))
    smap = await _scope_map(db)
    return [_item_brief(i, smap) for i in items]


# ---------- Tools de escritura ----------

async def pulso_crear(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    scope = await _resolve_scope(db, args["scope_name"], create=True)
    thread_id = None
    ref = args.get("hilo_id") or args.get("thread_id")
    if ref:
        from app.threads.models import Thread
        thread = await db.get(Thread, uuid.UUID(ref))
        if thread is None:
            raise ToolError(f"Hilo no encontrado: {ref}")
        thread_id = thread.id
    item = Item(
        scope_id=scope.id, title=args["title"], type=args["type"],
        summary_md=args.get("summary"), status="backlog",
        impact_ai=args.get("impact_ai"), effort_ai=args.get("effort_ai"),
        origen=args.get("origen", "ia-sesion"), created_by=await actor_for(db, token),
        thread_id=thread_id,
    )
    db.add(item)
    await db.flush()
    return _item_brief(item, {str(scope.id): scope.name})


async def pulso_avanzar(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    item = await _resolve_item(db, args.get("item_id") or args["query"])
    to = args["to_status"]
    if not valid_transition(item.status, to):
        raise ToolError(f"Transición inválida: {item.status} → {to}")
    try:
        await service.apply_transition(db, item, to, await actor_for(db, token))
    except service.TransitionError as e:
        raise ToolError(str(e)) from e
    return _item_brief(item, await _scope_map(db))


async def pulso_completar(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    item = await _resolve_item(db, args.get("item_id") or args["search_query"])
    try:
        unblocked = await service.close_item(
            db, item, "hecho", args.get("nota"), await actor_for(db, token),
            commit_sha=args.get("commit_sha"),
        )
    except service.TransitionError as e:
        raise ToolError(str(e)) from e
    return {**_item_brief(item, await _scope_map(db)), "unblocked": unblocked}


async def pulso_scopes(db: AsyncSession, token: ApiToken, args: dict) -> list[dict]:
    """Lista los scopes (agrupadores) con su descripción, conteo de ítems y ejemplos —
    para elegir el scope correcto en vez de adivinar por una palabra."""
    rows = (await db.execute(text("""
        SELECT s.name, s.description,
               count(i.id) FILTER (WHERE i.status NOT IN ('hecho','descartado')) AS abiertos,
               count(i.id) AS total,
               (array_agg(i.title ORDER BY i.created_at DESC)
                FILTER (WHERE i.status NOT IN ('hecho','descartado')))[1:3] AS ejemplos
        FROM scopes s LEFT JOIN items i ON i.scope_id = s.id
        WHERE s.archived = false
        GROUP BY s.id, s.name, s.description
        ORDER BY abiertos DESC, s.name
    """))).mappings().all()
    return [
        {"name": r["name"], "description": r["description"],
         "items_abiertos": r["abiertos"], "items_total": r["total"],
         "ejemplos": list(r["ejemplos"] or [])}
        for r in rows
    ]


async def pulso_mover_scope(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    """Mueve un ítem a otro scope EXISTENTE (match case-insensitive)."""
    item = await _resolve_item(db, args.get("item_id") or args["query"])
    scope = await _resolve_scope(db, args["scope_name"], create=False)
    item.scope_id = scope.id
    await db.flush()
    return _item_brief(item, {str(scope.id): scope.name})


async def pulso_relacionar(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    source = await _resolve_item(db, args.get("source_id") or args["source_query"])
    target = await _resolve_item(db, args.get("target_id") or args["target_query"])
    try:
        rel = await relationships.create_relationship(
            db, source.id, target.id, args["relation"], args.get("note")
        )
    except relationships.RelationshipError as e:
        raise ToolError(str(e)) from e
    return {"source_id": str(rel.source_id), "target_id": str(rel.target_id), "relation": rel.relation}


# ---------- Tools de Hilos ----------

def _thread_brief(t: Any) -> dict[str, Any]:
    return {"id": str(t.id), "title": t.title, "stage": t.stage, "scope_id": str(t.scope_id)}


async def pulso_hilo_crear(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    from app.threads import service as tservice

    t = await tservice.create_thread(db, args["scope_name"], args["title"], args.get("summary"))
    return _thread_brief(t)


async def pulso_hilo_avanzar(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    from app.threads import service as tservice

    t = await tservice.get_thread(db, uuid.UUID(args["thread_id"]))
    if t is None:
        raise ToolError("Hilo no encontrado.")
    artifact = args.get("artifact")
    content = artifact.get("content") if isinstance(artifact, dict) else None
    try:
        await tservice.advance_stage(db, t, content, await actor_user_id(db, token))
    except tservice.ThreadError as e:
        raise ToolError(str(e)) from e
    return _thread_brief(t)


async def pulso_hilo_listar(db: AsyncSession, token: ApiToken, args: dict) -> list[dict]:
    from app.threads import service as tservice

    threads = await tservice.list_threads(db, args.get("stage"), args.get("scope"))
    return [_thread_brief(t) for t in threads]


async def pulso_hilo(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    """Detalle de un Hilo: stage, artefactos e ítems vinculados (por thread_id)."""
    from app.threads.models import Thread, ThreadArtifact

    thread = await db.get(Thread, uuid.UUID(args["id"]))
    if thread is None:
        raise ToolError("Hilo no encontrado.")
    arts = (await db.execute(
        select(ThreadArtifact).where(ThreadArtifact.thread_id == thread.id)
        .order_by(ThreadArtifact.created_at)
    )).scalars().all()
    items = (await db.execute(
        select(Item).where(Item.thread_id == thread.id).order_by(Item.created_at)
    )).scalars().all()
    smap = await _scope_map(db)
    return {
        **_thread_brief(thread),
        "summary_md": thread.summary_md,
        "artefactos": [{"stage": a.stage, "kind": a.kind, "content_md": a.content_md} for a in arts],
        "items": [_item_brief(i, smap) for i in items],
    }


async def pulso_hilo_vincular(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    """Vincula un ítem existente a un Hilo (set thread_id). Acepta item_id o query."""
    from app.threads.models import Thread

    ref = args.get("hilo_id") or args["thread_id"]
    thread = await db.get(Thread, uuid.UUID(ref))
    if thread is None:
        raise ToolError(f"Hilo no encontrado: {ref}")
    item = await _resolve_item(db, args.get("item_id") or args["query"])
    item.thread_id = thread.id
    await db.flush()
    return {**_item_brief(item, await _scope_map(db)), "hilo_id": str(thread.id),
            "hilo_title": thread.title}


async def actor_user_id(db: AsyncSession, token: ApiToken) -> uuid.UUID | None:
    """user_id del creador del token (para autoría de artefactos), o None."""
    user = (await db.execute(select(User).where(User.id == token.created_by))).scalar_one_or_none()
    return user.id if user else None


# ---------- Tools de Incidentes (errores de Sentry) ----------

async def pulso_incidentes(db: AsyncSession, token: ApiToken, args: dict) -> list[dict]:
    """Lista los incidentes del contenedor de errores de Sentry."""
    from app.webhooks.models import SentryIssue

    q = select(SentryIssue).order_by(SentryIssue.last_seen.desc().nulls_last())
    status = args.get("status", "new")
    if status and status != "todos":
        q = q.where(SentryIssue.status == status)
    rows = (await db.execute(q.limit(int(args.get("limit", 30))))).scalars().all()
    return [
        {"id": str(i.id), "sentry_issue_id": i.sentry_issue_id, "title": i.title,
         "project": i.project, "level": i.level, "events": i.events_count,
         "triage": i.triage, "status": i.status,
         "first_seen": i.first_seen.isoformat() if i.first_seen else None,
         "last_seen": i.last_seen.isoformat() if i.last_seen else None,
         "web_url": (i.payload or {}).get("web_url") if isinstance(i.payload, dict) else None}
        for i in rows
    ]


async def pulso_incidente(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    """Detalle de un incidente con stack trace (lo trae de la API de Sentry)."""
    from app.webhooks import service as wservice
    from app.webhooks.models import SentryIssue

    issue = await db.get(SentryIssue, uuid.UUID(args["id"]))
    if issue is None:
        raise ToolError("Incidente no encontrado.")
    out = {"id": str(issue.id), "sentry_issue_id": issue.sentry_issue_id, "title": issue.title,
           "project": issue.project, "level": issue.level, "events": issue.events_count,
           "triage": issue.triage, "status": issue.status,
           "first_seen": issue.first_seen.isoformat() if issue.first_seen else None,
           "last_seen": issue.last_seen.isoformat() if issue.last_seen else None,
           "web_url": (issue.payload or {}).get("web_url") if isinstance(issue.payload, dict) else None}
    try:
        detail = await wservice.fetch_issue_detail(issue.sentry_issue_id)
        out["stacktrace"] = detail.get("stacktrace")
        out["culprit"] = detail.get("culprit")
    except Exception as e:
        out["stacktrace"] = None
        out["detail_error"] = f"No se pudo traer el stack trace: {str(e)[:160]}"
    return out


async def pulso_incidente_resolver(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    """Marca un incidente como resuelto en Pulso y (opcional) en Sentry."""
    from app.webhooks import service as wservice
    from app.webhooks.models import SentryIssue

    issue = await db.get(SentryIssue, uuid.UUID(args["id"]))
    if issue is None:
        raise ToolError("Incidente no encontrado.")
    issue.status = "resolved"
    sentry_done = False
    if args.get("resolver_en_sentry", True):
        sentry_done = await wservice.resolve_in_sentry(issue.sentry_issue_id)
    # Si tenía ítem de backlog asociado, cerrarlo también.
    if issue.item_id is not None:
        item = await service.get_item(db, issue.item_id)
        if item is not None and item.status not in ("hecho", "descartado"):
            try:
                await service.close_item(
                    db, item, "hecho", args.get("nota") or "resuelto desde incidente",
                    await actor_for(db, token), commit_sha=args.get("commit_sha"),
                )
            except service.TransitionError:
                pass
    return {"id": str(issue.id), "status": "resolved", "resuelto_en_sentry": sentry_done}
