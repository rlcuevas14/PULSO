"""Implementación de las tools MCP de Pulso.

Cada tool reutiliza la lógica de servicio (lifecycle, grafo, relaciones) para no
divergir de la UI/REST. Las business-errors se propagan como ToolError → isError.
"""

import logging
import uuid
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import ApiToken, User
from app.enums import EFFORTS, ITEM_TYPES, OPEN_STATUSES, ORIGENES
from app.items import graph, relationships, service
from app.items.lifecycle import valid_transition
from app.items.models import Item
from app.items.search import search_items
from app.scopes import service as scopes_service
from app.scopes.models import Scope

logger = logging.getLogger("pulso.mcp.tools")

# Estados abiertos (no terminales), tomados de la única fuente de verdad de dominios.
_OPEN = list(OPEN_STATUSES)


class ToolError(Exception):
    """Error de negocio de una tool (se devuelve como isError, no error JSON-RPC)."""


def _uuid_or_error(ref: Any, field: str = "id") -> uuid.UUID:
    """Convierte `ref` a UUID o lanza ToolError con un mensaje accionable.

    Centraliza el parseo de UUID de las tools que reciben un id explícito (hilo_id,
    thread_id, id de incidente, etc.), para no propagar un ValueError crudo.
    """
    try:
        return uuid.UUID(str(ref))
    except (ValueError, AttributeError, TypeError) as e:
        raise ToolError(f"{field} no es un UUID válido: «{ref}».") from e


async def actor_for(db: AsyncSession, token: ApiToken) -> str:
    """Resuelve el actor de una escritura: email del creador del token, o token:<name>."""
    user = (await db.execute(select(User).where(User.id == token.created_by))).scalar_one_or_none()
    return user.email if user else f"token:{token.name}"


async def _resolve_scope(db: AsyncSession, name: str, create: bool = False) -> Scope:
    """Resuelve/crea un scope reutilizando el servicio único (case-insensitive).

    Traduce ScopeError (nombre vacío / no existe) a ToolError para que llegue como
    isError con un mensaje útil, no como excepción cruda.
    """
    try:
        return await scopes_service.resolve_scope(
            db, name, create=create, source_repo="mcp" if create else None
        )
    except scopes_service.ScopeError as e:
        raise ToolError(str(e)) from e


async def _scope_exists(db: AsyncSession, name: str) -> bool:
    """True si ya existe un scope con ese nombre (match case-insensitive)."""
    cleaned = (name or "").strip()
    if not cleaned:
        return False
    found = (await db.execute(
        select(Scope.id).where(func.lower(Scope.name) == cleaned.lower())
    )).first()
    return found is not None


async def _scope_map(db: AsyncSession) -> dict[str, str]:
    rows = (await db.execute(select(Scope.id, Scope.name))).all()
    return {str(r[0]): r[1] for r in rows}


async def _resolve_item(db: AsyncSession, ref: str) -> Item:
    """Acepta un UUID o una query de texto (resuelta por full-text con abort por ambigüedad)."""
    try:
        item_id = uuid.UUID(str(ref))
    except (ValueError, AttributeError, TypeError):
        try:
            item_id = await relationships.resolve_query(db, ref)
        except relationships.RelationshipError as e:
            raise ToolError(str(e)) from e
    item = await service.get_item(db, item_id)
    if item is None:
        raise ToolError(f"Ítem no encontrado: {ref}")
    return item


async def _resolve_item_verbose(db: AsyncSession, item_id: str | None, query: str | None,
                                *, what: str = "item_id o query") -> tuple[Item, str | None]:
    """Resuelve un ítem por id o por texto, devolviendo (item, advertencia_o_None).

    Si llega un item_id explícito, se usa tal cual (sin advertencia). Si llega una query
    de texto, se resuelve con `resolve_query_verbose`: aborta ante ambigüedad exacta y, si
    el match es de baja confianza, devuelve una advertencia (no aborta) para que el caller
    la incluya en el resultado.
    """
    if item_id:
        return await _resolve_item(db, item_id), None
    if not query:
        raise ToolError(f"Debes pasar {what}.")
    try:
        resolved = await relationships.resolve_query_verbose(db, query)
    except relationships.RelationshipError as e:
        raise ToolError(str(e)) from e
    item = await service.get_item(db, resolved["id"])
    if item is None:
        raise ToolError(f"Ítem no encontrado: {query}")
    warning = None
    if resolved.get("low_confidence"):
        warning = (
            f"resolví «{query}» → «{resolved['title']}» con baja confianza; "
            "pasa item_id para confirmar."
        )
    return item, warning


def _item_brief(i: Item, scope_map: dict[str, str] | None = None) -> dict[str, Any]:
    """DTO consistente de un ítem (RET-01): SIEMPRE incluye thread_id y scope (null si no)."""
    return {
        "id": str(i.id), "title": i.title, "type": i.type, "status": i.status,
        "priority": i.priority, "impact_ai": i.impact_ai, "effort_ai": i.effort_ai,
        "scope_id": str(i.scope_id), "origen": i.origen,
        "scope": scope_map.get(str(i.scope_id)) if scope_map is not None else None,
        "thread_id": str(i.thread_id) if i.thread_id is not None else None,
    }


# ---------- Tools de lectura ----------

async def pulso_contexto(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    scope_name = args.get("scope")
    work = args.get("work_description")
    scope = None
    if scope_name:
        scope = (await db.execute(
            select(Scope).where(func.lower(Scope.name) == str(scope_name).strip().lower())
        )).scalar_one_or_none()

    scope_id = scope.id if scope else None

    # Quickwins (con fallback a prioridad humana si no hay impacto IA — degradación sin F2).
    qw = await service.list_items(
        db, scope=scope_id, statuses=_OPEN, quickwins=True, order="impacto", limit=5
    )
    if not qw:
        base = select(Item).where(Item.status.in_(_OPEN))
        if scope_id:
            base = base.where(Item.scope_id == scope_id)
        qw = list((await db.execute(
            base.where(Item.priority.in_(["p0", "p1"])).order_by(Item.priority).limit(5)
        )).scalars().all())

    blockers = await service.list_items(
        db, scope=scope_id, statuses=["bloqueado"], order="impacto", limit=10
    )

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
    limit = int(args.get("limit", 10))
    scope_name = (args.get("scope") or "").strip() or None
    tipo = (args.get("tipo") or "").strip() or None

    # FTS único (app.items.search). Pedimos de más cuando hay filtros post-query, para no
    # devolver menos de `limit` por haber filtrado parte del top-N por scope/tipo.
    fetch = limit * 4 if (scope_name or tipo) else limit
    rows = await search_items(db, q, limit=max(fetch, limit), with_scope=True)

    if scope_name:
        rows = [r for r in rows if (r.get("scope") or "").lower() == scope_name.lower()]
    if tipo:
        rows = [r for r in rows if r.get("type") == tipo]
    rows = rows[:limit]

    return [
        {"id": r["id"], "title": r["title"], "summary_md": r.get("summary_md"),
         "type": r["type"], "status": r["status"], "scope_id": r.get("scope_id"),
         "scope": r.get("scope"), "effort_ai": r.get("effort_ai"),
         "impact_ai": r.get("impact_ai")}
        for r in rows
    ]


async def pulso_listar(db: AsyncSession, token: ApiToken, args: dict) -> list[dict]:
    items = await service.list_items(
        db,
        scope=(args.get("scope") or None),
        statuses=args.get("status") or None,
        type=(args.get("tipo") or None),
        order=args.get("order", "impacto"),
        quickwins=bool(args.get("quickwins")),
        limit=int(args.get("limit", 20)),
    )
    smap = await _scope_map(db)
    return [_item_brief(i, smap) for i in items]


# ---------- Tools de escritura ----------

async def pulso_crear(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    # --- Validación temprana (mejor mensaje que el catch-all de IntegrityError) ---
    title = (args.get("title") or "").strip()
    if not title:
        raise ToolError("El título (title) no puede estar vacío.")
    scope_name = (args.get("scope_name") or "").strip()
    if not scope_name:
        raise ToolError("El scope (scope_name) no puede estar vacío.")

    item_type = args.get("type")
    if item_type not in ITEM_TYPES:
        raise ToolError(f"type inválido «{item_type}»; usa uno de: {', '.join(ITEM_TYPES)}.")

    origen = args.get("origen", "ia-sesion")
    if origen not in ORIGENES:
        raise ToolError(f"origen inválido «{origen}»; usa uno de: {', '.join(ORIGENES)}.")

    effort_ai = args.get("effort_ai")
    if effort_ai is not None and effort_ai not in EFFORTS:
        raise ToolError(f"effort_ai inválido «{effort_ai}»; usa uno de: {', '.join(EFFORTS)} (o null).")

    impact_ai = args.get("impact_ai")
    if impact_ai is not None:
        if not isinstance(impact_ai, int) or isinstance(impact_ai, bool) or not (1 <= impact_ai <= 5):
            raise ToolError("impact_ai debe ser un entero entre 1 y 5 (o null).")

    # --- Resolución de scope (registrando si se creó uno nuevo) ---
    scope_existed = await _scope_exists(db, scope_name)
    scope = await _resolve_scope(db, scope_name, create=True)
    scope_creado = not scope_existed

    # --- Resolución del hilo (opcional) ---
    thread_id = None
    ref = args.get("hilo_id") or args.get("thread_id")
    if ref:
        from app.threads.models import Thread
        thread = await db.get(Thread, _uuid_or_error(ref, "hilo_id"))
        if thread is None:
            raise ToolError(f"Hilo no encontrado: {ref}")
        thread_id = thread.id

    # --- IDEM-01: idempotencia. Si ya hay un ítem ABIERTO con el mismo title+scope,
    #     devolverlo en vez de duplicar (protege ante reintentos). ---
    existing = (await db.execute(
        select(Item).where(
            Item.scope_id == scope.id,
            func.lower(Item.title) == title.lower(),
            Item.status.in_(_OPEN),
        ).limit(1)
    )).scalar_one_or_none()
    if existing is not None:
        return {**_item_brief(existing, {str(scope.id): scope.name}),
                "ya_existia": True, "scope_creado": scope_creado}

    item = Item(
        scope_id=scope.id, title=title, type=item_type,
        summary_md=args.get("summary"), status="backlog",
        impact_ai=impact_ai, effort_ai=effort_ai,
        origen=origen, created_by=await actor_for(db, token),
        thread_id=thread_id,
    )
    db.add(item)
    await db.flush()
    return {**_item_brief(item, {str(scope.id): scope.name}),
            "ya_existia": False, "scope_creado": scope_creado}


async def pulso_avanzar(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    if "to_status" not in args:
        raise ToolError("Falta to_status (estado destino).")
    item, warning = await _resolve_item_verbose(db, args.get("item_id"), args.get("query"))
    to = args["to_status"]
    if not valid_transition(item.status, to):
        raise ToolError(f"Transición inválida: {item.status} → {to}")
    try:
        await service.apply_transition(db, item, to, await actor_for(db, token))
    except service.TransitionError as e:
        raise ToolError(str(e)) from e
    out = _item_brief(item, await _scope_map(db))
    if warning:
        out["advertencia"] = warning
    return out


async def pulso_completar(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    item, warning = await _resolve_item_verbose(
        db, args.get("item_id"), args.get("search_query"), what="item_id o search_query"
    )
    try:
        unblocked = await service.close_item(
            db, item, "hecho", args.get("nota"), await actor_for(db, token),
            commit_sha=args.get("commit_sha"),
        )
    except service.TransitionError as e:
        raise ToolError(str(e)) from e
    out = {**_item_brief(item, await _scope_map(db)), "unblocked": unblocked}
    if warning:
        out["advertencia"] = warning
    return out


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
    if not (args.get("scope_name") or "").strip():
        raise ToolError("Falta scope_name (scope destino).")
    item, warning = await _resolve_item_verbose(db, args.get("item_id"), args.get("query"))
    scope = await _resolve_scope(db, args["scope_name"], create=False)
    item.scope_id = scope.id
    await db.flush()
    out = _item_brief(item, {str(scope.id): scope.name})
    if warning:
        out["advertencia"] = warning
    return out


async def pulso_relacionar(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    if "relation" not in args:
        raise ToolError("Falta relation (tipo de arco).")
    source, w_src = await _resolve_item_verbose(
        db, args.get("source_id"), args.get("source_query"), what="source_id o source_query"
    )
    target, w_tgt = await _resolve_item_verbose(
        db, args.get("target_id"), args.get("target_query"), what="target_id o target_query"
    )
    try:
        rel = await relationships.create_relationship(
            db, source.id, target.id, args["relation"], args.get("note")
        )
    except relationships.RelationshipError as e:
        raise ToolError(str(e)) from e
    out = {"source_id": str(rel.source_id), "target_id": str(rel.target_id),
           "relation": rel.relation}
    advertencias = [w for w in (w_src, w_tgt) if w]
    if advertencias:
        out["advertencia"] = " | ".join(advertencias)
    return out


# ---------- Tools de Hilos ----------

def _thread_brief(t: Any) -> dict[str, Any]:
    return {"id": str(t.id), "title": t.title, "stage": t.stage, "scope_id": str(t.scope_id)}


async def pulso_hilo_crear(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    from app.threads import service as tservice

    t = await tservice.create_thread(db, args["scope_name"], args["title"], args.get("summary"))
    return _thread_brief(t)


async def pulso_hilo_avanzar(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    from app.threads import service as tservice

    t = await tservice.get_thread(db, _uuid_or_error(args.get("thread_id"), "thread_id"))
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

    thread = await db.get(Thread, _uuid_or_error(args.get("id"), "id"))
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

    ref = args.get("hilo_id") or args.get("thread_id")
    if not ref:
        raise ToolError("Falta hilo_id (id del Hilo al que vincular).")
    thread = await db.get(Thread, _uuid_or_error(ref, "hilo_id"))
    if thread is None:
        raise ToolError(f"Hilo no encontrado: {ref}")
    item, warning = await _resolve_item_verbose(db, args.get("item_id"), args.get("query"))
    item.thread_id = thread.id
    await db.flush()
    out = {**_item_brief(item, await _scope_map(db)), "hilo_id": str(thread.id),
           "hilo_title": thread.title}
    if warning:
        out["advertencia"] = warning
    return out


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

    issue = await db.get(SentryIssue, _uuid_or_error(args.get("id"), "id"))
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
        logger.warning("pulso_incidente: fallo al traer stack trace de %s: %s",
                       issue.sentry_issue_id, e)
        out["stacktrace"] = None
        out["detail_error"] = f"No se pudo traer el stack trace: {str(e)[:160]}"
    return out


async def pulso_incidente_resolver(db: AsyncSession, token: ApiToken, args: dict) -> dict:
    """Marca un incidente como resuelto en Pulso y (opcional) en Sentry.

    Reutiliza la lógica de servicio única (ARCH-2: webhooks.service.resolve_issue) para no
    duplicar la cascada (resolver en Sentry + cerrar el ítem de backlog ligado).
    """
    from app.webhooks import service as wservice
    from app.webhooks.models import SentryIssue

    issue = await db.get(SentryIssue, _uuid_or_error(args.get("id"), "id"))
    if issue is None:
        raise ToolError("Incidente no encontrado.")
    return await wservice.resolve_issue(
        db, issue,
        in_sentry=bool(args.get("resolver_en_sentry", True)),
        nota=args.get("nota"),
        actor=await actor_for(db, token),
        commit_sha=args.get("commit_sha"),
    )
