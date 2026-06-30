"""Lógica de webhooks: verificación de firma HMAC + ingesta Sentry + progreso Git."""

import hashlib
import hmac
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.items import service
from app.items.models import Item, ItemEvent
from app.jobs.models import AgentRun
from app.scopes.service import resolve_scope
from app.webhooks.models import SentryIssue

logger = logging.getLogger("pulso.webhooks")

_TAG_RE = re.compile(r"<[^>]+>")
_PULSO_RE = re.compile(r"(?:closes\s+)?pulso:([0-9a-fA-F-]{36})")


def verify_sentry_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not secret or not header:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def verify_github_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not secret or not header:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def _sanitize(textval: str | None, limit: int = 4000) -> str:
    if not textval:
        return ""
    return _TAG_RE.sub("", textval)[:limit]


def _parse_dt(value: Any) -> datetime | None:
    """Parsea un timestamp ISO de Sentry (con o sin 'Z'). None si no se puede."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


async def ingest_sentry(db: AsyncSession, payload: dict) -> dict:
    """Upsert idempotente en sentry_issues por sentry_issue_id (UNIQUE).

    Política: el error aterriza en el CONTENEDOR sentry_issues (no en el backlog). La
    promoción al backlog requiere análisis: la hace el triage IA (clasifica el ruido) y/o
    el owner manualmente desde /incidentes. Dedup: el mismo issue incrementa events_count.
    """
    data = payload.get("data", {}).get("issue") or payload.get("issue") or payload
    sentry_id = str(data.get("id") or payload.get("id") or "")
    if not sentry_id:
        logger.warning("ingest_sentry: payload sin id de issue, se descarta")
        raise ValueError("Falta el id del issue de Sentry")

    title = _sanitize(data.get("title") or data.get("culprit") or "Sentry issue", 500)
    project = str(data.get("project", {}).get("slug") if isinstance(data.get("project"), dict)
                  else data.get("project") or "desconocido")[:60]
    level = data.get("level", "error")
    if level not in ("error", "warning", "info"):
        level = "error"
    web_url = data.get("web_url") or data.get("permalink")

    issue = (await db.execute(
        select(SentryIssue).where(SentryIssue.sentry_issue_id == sentry_id)
    )).scalar_one_or_none()
    try:
        events = int(str(data.get("count") or 1))
    except (TypeError, ValueError):
        events = 1
    now = datetime.now(timezone.utc)
    # Fechas REALES del error en Sentry (no la hora de ingesta).
    first_seen = _parse_dt(data.get("firstSeen")) or now
    last_seen = _parse_dt(data.get("lastSeen")) or now
    if issue is None:
        issue = SentryIssue(
            sentry_issue_id=sentry_id, project=project, title=title, level=level,
            status="new", events_count=events, first_seen=first_seen, last_seen=last_seen,
            payload={"sanitized_title": title, "web_url": web_url},
        )
        db.add(issue)
        created = True
        await db.flush()
        # Encolar triage IA (pre-clasifica el ruido; corre cuando hay ANTHROPIC_API_KEY).
        db.add(AgentRun(kind="triage-sentry", ref_type="sentry_issue",
                        ref_id=issue.id, status="pendiente"))
    else:
        issue.events_count += 1
        issue.last_seen = last_seen
        created = False
    await db.flush()

    return {"sentry_issue_id": sentry_id, "created": created,
            "events_count": issue.events_count, "triage": issue.triage, "status": issue.status}


async def promote_issue(
    db: AsyncSession, issue: SentryIssue, priority: str = "p1", actor: str = "manual"
) -> str:
    """Promueve un issue del contenedor al backlog como ítem bug (decisión con análisis:
    triage IA o el owner). Idempotente: si ya está linkeado, devuelve el ítem existente."""
    if issue.item_id is not None:
        return str(issue.item_id)
    scope = await resolve_scope(db, issue.project, create=True, source_repo="sentry")
    item = Item(
        scope_id=scope.id, title=issue.title[:300], type="bug", status="backlog",
        summary_md=_sanitize(str(issue.payload), 4000), origen="sentry",
        priority=priority, priority_declared=priority, stale_risk=True,
        source_refs={"sentry_issue_id": issue.sentry_issue_id},
    )
    db.add(item)
    await db.flush()
    issue.item_id = item.id
    issue.status = "linked"
    db.add(ItemEvent(item_id=item.id, actor=f"sentry:{issue.sentry_issue_id}",
                     action="created", payload={"from": "sentry", "priority": priority, "by": actor}))
    await db.flush()
    return str(item.id)


async def resolve_issue(
    db: AsyncSession,
    issue: SentryIssue,
    *,
    in_sentry: bool,
    nota: str | None,
    actor: str,
    commit_sha: str | None = None,
) -> dict[str, Any]:
    """Resuelve un incidente: lo marca resuelto en Pulso, opcionalmente en Sentry, y
    cierra el ítem de backlog ligado (si lo hay y sigue abierto).

    Lógica de servicio extraída de la tool MCP pulso_incidente_resolver (ARCH-2) para que
    UI / REST / MCP la consuman sin duplicar. Solo flush; el commit lo hace el borde.

    Devuelve {"id", "status", "resuelto_en_sentry", "item_cerrado"}.
    """
    issue.status = "resolved"
    sentry_done = False
    if in_sentry:
        try:
            sentry_done = await resolve_in_sentry(issue.sentry_issue_id)
        except Exception as e:  # error de red / API de Sentry: no debe bloquear el cierre local
            logger.warning(
                "resolve_issue: fallo al resolver %s en Sentry: %s",
                issue.sentry_issue_id, e,
            )
            sentry_done = False

    item_cerrado = False
    if issue.item_id is not None:
        item = await service.get_item(db, issue.item_id)
        if item is not None and item.status not in ("done", "discarded"):
            try:
                await service.close_item(
                    db, item, "done", nota or "resolved from incident",
                    actor, commit_sha=commit_sha,
                )
                item_cerrado = True
            except service.TransitionError as e:
                logger.warning(
                    "resolve_issue: no se pudo cerrar el ítem %s ligado al incidente %s: %s",
                    issue.item_id, issue.id, e,
                )

    await db.flush()
    return {
        "id": str(issue.id),
        "status": "resolved",
        "resuelto_en_sentry": sentry_done,
        "item_cerrado": item_cerrado,
    }


async def process_github_push(db: AsyncSession, payload: dict) -> dict:
    """Marca last_touched_at por scope y autocompleta ítems referenciados por pulso:UUID."""
    commits = payload.get("commits", [])
    touched_scopes: set[str] = set()
    completed: list[str] = []

    for commit in commits:
        msg = commit.get("message", "")
        sha = commit.get("id", "")
        # scope del conventional commit: fix(auth): -> auth
        m = re.match(r"^\w+\(([\w-]+)\)", msg)
        if m:
            touched_scopes.add(m.group(1))
        # pulso:UUID -> completar (idempotente, validado)
        for match in _PULSO_RE.finditer(msg):
            item_id = match.group(1)
            done = await _complete_by_ref(db, item_id, sha)
            if done:
                completed.append(item_id)

    for scope_name in touched_scopes:
        await db.execute(text("""
            UPDATE items SET last_touched_at = now()
            WHERE scope_id = (SELECT id FROM scopes WHERE name = :name)
              AND status NOT IN ('done','discarded')
        """), {"name": scope_name})

    await db.flush()
    return {"touched_scopes": sorted(touched_scopes), "completed": completed}


async def fetch_sentry_issues(
    token: str, org: str, project: str, query: str = "is:unresolved", limit: int = 100
) -> list[dict[str, Any]]:
    """Trae issues del proyecto desde la API de Sentry (para backfill del histórico)."""
    url = f"https://sentry.io/api/0/projects/{org}/{project}/issues/"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(
            url, params={"query": query, "limit": min(limit, 100)},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        data = resp.json()
    return data if isinstance(data, list) else []


async def backfill_issues(db: AsyncSession, issues: list[dict], project: str) -> dict:
    """Ingiere al contenedor cada issue traído de la API de Sentry (dedup por id)."""
    ingested = 0
    for iss in issues:
        payload = {"data": {"issue": {
            "id": iss.get("id"),
            "title": iss.get("title") or iss.get("culprit") or "Sentry issue",
            "project": project,
            "level": iss.get("level", "error"),
            "web_url": iss.get("permalink"),
            "count": iss.get("count"),
            "firstSeen": iss.get("firstSeen"),
            "lastSeen": iss.get("lastSeen"),
        }}}
        try:
            await ingest_sentry(db, payload)
            ingested += 1
        except ValueError:
            continue  # issue sin id → saltar
    return {"ingested": ingested, "total": len(issues)}


def _format_stacktrace(event: dict, max_frames: int = 12) -> str:
    """Extrae un resumen legible (excepción + frames in-app) del evento de Sentry."""
    if not event:
        return "(sin evento)"
    lines: list[str] = []
    culprit = event.get("culprit")
    if culprit:
        lines.append(f"culprit: {culprit}")
    for entry in event.get("entries", []):
        if entry.get("type") != "exception":
            continue
        for val in entry.get("data", {}).get("values", []):
            etype = val.get("type", "")
            evalue = val.get("value", "")
            lines.append(f"\n{etype}: {evalue}")
            frames = (val.get("stacktrace") or {}).get("frames") or []
            # priorizar frames del propio código (in_app)
            in_app = [f for f in frames if f.get("inApp")] or frames
            for f in in_app[-max_frames:]:
                fn = f.get("filename") or f.get("module") or "?"
                ln = f.get("lineNo")
                func = f.get("function") or "?"
                lines.append(f"  {fn}:{ln} in {func}")
                ctx = f.get("context") or []
                for _, code in ctx:
                    if isinstance(code, str) and code.strip():
                        lines.append(f"      {code.strip()[:160]}")
    return "\n".join(lines)[:6000] if lines else "(sin stack trace)"


async def fetch_issue_detail(issue_id: str) -> dict[str, Any]:
    """Trae metadata + stack trace del último evento de un issue de Sentry (para el MCP)."""
    token = settings.sentry_api_token
    if not token:
        raise RuntimeError("SENTRY_API_TOKEN no configurado")
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=60) as client:
        meta_r = await client.get(f"https://sentry.io/api/0/issues/{issue_id}/", headers=headers)
        meta_r.raise_for_status()
        meta = meta_r.json()
        ev_r = await client.get(
            f"https://sentry.io/api/0/issues/{issue_id}/events/latest/", headers=headers
        )
        event = ev_r.json() if ev_r.status_code == 200 else {}
    return {
        "title": meta.get("title"),
        "culprit": meta.get("culprit"),
        "level": meta.get("level"),
        "count": meta.get("count"),
        "first_seen": meta.get("firstSeen"),
        "last_seen": meta.get("lastSeen"),
        "permalink": meta.get("permalink"),
        "stacktrace": _format_stacktrace(event),
    }


async def resolve_in_sentry(issue_id: str) -> bool:
    """Marca el issue como resuelto en Sentry (requiere token con Issue&Event: Write)."""
    token = settings.sentry_api_token
    if not token:
        return False
    headers = {"Authorization": f"Bearer {token}", "content-type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.put(
            f"https://sentry.io/api/0/issues/{issue_id}/", headers=headers,
            json={"status": "resolved"},
        )
        return r.status_code in (200, 202)


async def _complete_by_ref(db: AsyncSession, item_id: str, sha: str) -> bool:
    try:
        item = await service.get_item(db, uuid.UUID(item_id))
    except (ValueError, AttributeError):
        logger.warning("_complete_by_ref: item_id inválido en commit %s: %r", sha[:12], item_id)
        return False
    if item is None:
        return False
    if item.status in ("done", "discarded"):
        return False  # idempotent: already closed
    try:
        await service.close_item(db, item, "done", f"closed by commit {sha[:12]}",
                                 f"github:{sha[:12]}", commit_sha=sha)
    except service.TransitionError as e:
        logger.warning("_complete_by_ref: transición inválida al cerrar %s: %s", item_id, e)
        return False
    return True
