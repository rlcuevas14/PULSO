"""Lógica de webhooks: verificación de firma HMAC + ingesta Sentry + progreso Git."""

import hashlib
import hmac
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.items.models import Item, ItemEvent
from app.jobs.models import AgentRun
from app.webhooks.models import SentryIssue

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


async def ingest_sentry(db: AsyncSession, payload: dict) -> dict:
    """Upsert idempotente en sentry_issues por sentry_issue_id (UNIQUE).

    Política: el error aterriza en el CONTENEDOR sentry_issues (no en el backlog). La
    promoción al backlog requiere análisis: la hace el triage IA (clasifica el ruido) y/o
    el owner manualmente desde /incidentes. Dedup: el mismo issue incrementa events_count.
    """
    data = payload.get("data", {}).get("issue") or payload.get("issue") or payload
    sentry_id = str(data.get("id") or payload.get("id") or "")
    if not sentry_id:
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
    now = datetime.now(timezone.utc)
    if issue is None:
        issue = SentryIssue(
            sentry_issue_id=sentry_id, project=project, title=title, level=level,
            status="new", events_count=1, first_seen=now, last_seen=now,
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
        issue.last_seen = now
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
    scope = await _get_or_create_scope(db, issue.project)
    item = Item(
        scope_id=scope, title=issue.title[:300], type="bug", status="backlog",
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


async def _get_or_create_scope(db: AsyncSession, name: str) -> uuid.UUID:
    from app.scopes.models import Scope
    scope = (await db.execute(select(Scope).where(Scope.name == name))).scalar_one_or_none()
    if scope is None:
        scope = Scope(name=name[:60], source_repo="sentry")
        db.add(scope)
        await db.flush()
    return scope.id


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
              AND status NOT IN ('hecho','descartado')
        """), {"name": scope_name})

    await db.flush()
    return {"touched_scopes": sorted(touched_scopes), "completed": completed}


async def _complete_by_ref(db: AsyncSession, item_id: str, sha: str) -> bool:
    from app.items import service
    try:
        item = await service.get_item(db, uuid.UUID(item_id))
    except (ValueError, AttributeError):
        return False
    if item is None:
        return False
    if item.status in ("hecho", "descartado"):
        return False  # idempotente: ya cerrado
    try:
        await service.close_item(db, item, "hecho", f"cerrado por commit {sha[:12]}",
                                 f"github:{sha[:12]}", commit_sha=sha)
    except service.TransitionError:
        return False
    return True
