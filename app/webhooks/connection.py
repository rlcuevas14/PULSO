"""Conexión Sentry a nivel cuenta: ruteo por token, config, re-attach de no-matcheados.

Chokepoint de tenancy para webhooks (spec 2026-07-10 §4.2): todo lookup aquí va acotado
por account_id, así un token de webhook solo puede escribir dentro de su cuenta.
"""

import re
import secrets
import uuid

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.projects.models import Project
from app.webhooks.models import SentryConnection, SentryIssue

DEFAULT_BASE_URL = "https://sentry.io"
# Solo scheme://host[:port] — sin path/query (espeja la regla system.url-prefix de Sentry).
_BASE_URL_RE = re.compile(r"^https?://[A-Za-z0-9.-]+(:\d+)?$")


class SentryConfigError(ValueError):
    pass


async def get_by_token(db: AsyncSession, token: str) -> SentryConnection | None:
    return (await db.execute(
        select(SentryConnection).where(SentryConnection.webhook_token == token)
    )).scalar_one_or_none()


async def get_for_account(db: AsyncSession, account_id: uuid.UUID) -> SentryConnection | None:
    return (await db.execute(
        select(SentryConnection).where(SentryConnection.account_id == account_id)
    )).scalar_one_or_none()


async def get_or_create(db: AsyncSession, account_id: uuid.UUID) -> SentryConnection:
    conn = await get_for_account(db, account_id)
    if conn is None:
        conn = SentryConnection(account_id=account_id, webhook_token=secrets.token_urlsafe(32))
        db.add(conn)
        await db.flush()
    return conn


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


async def update_connection(
    db: AsyncSession, conn: SentryConnection, *,
    client_secret: str | None, api_token: str | None,
    org_slug: str | None, base_url: str | None,
) -> SentryConnection:
    base = _clean(base_url)
    if base and not _BASE_URL_RE.match(base):
        raise SentryConfigError("base_url must be http(s)://host[:port] with no path.")
    conn.client_secret = _clean(client_secret)
    conn.api_token = _clean(api_token)
    conn.org_slug = _clean(org_slug)
    conn.base_url = base
    await db.flush()
    return conn


async def regenerate_token(db: AsyncSession, conn: SentryConnection) -> SentryConnection:
    conn.webhook_token = secrets.token_urlsafe(32)
    await db.flush()
    return conn


def effective_base_url(conn: SentryConnection | None) -> str:
    return conn.base_url if (conn and conn.base_url) else DEFAULT_BASE_URL


async def outbound(db: AsyncSession, account_id: uuid.UUID | None) -> SentryConnection | None:
    """Conexión usable para llamadas salientes a la API de Sentry (feature B), o None."""
    if account_id is None:
        return None
    conn = await get_for_account(db, account_id)
    return conn if (conn and conn.api_token) else None


async def route_project(
    db: AsyncSession, account_id: uuid.UUID, slug: str | None
) -> Project | None:
    """slug → proyecto, acotado a la cuenta. slug=None solo enruta cuando la cuenta
    tiene exactamente un proyecto mapeado (los payloads event_alert no traen slug)."""
    if slug:
        return (await db.execute(
            select(Project).where(
                Project.account_id == account_id, Project.sentry_project_slug == slug
            )
        )).scalar_one_or_none()
    mapped = list((await db.execute(
        select(Project).where(
            Project.account_id == account_id, Project.sentry_project_slug.is_not(None)
        ).limit(2)
    )).scalars().all())
    return mapped[0] if len(mapped) == 1 else None


def _unmatched_filter(account_id: uuid.UUID):
    return (
        SentryIssue.project_id.is_(None),
        or_(SentryIssue.account_id == account_id, SentryIssue.account_id.is_(None)),
    )


async def count_unmatched(db: AsyncSession, account_id: uuid.UUID) -> int:
    return int(await db.scalar(
        select(func.count()).select_from(SentryIssue).where(*_unmatched_filter(account_id))
    ) or 0)


async def reattach_unmatched(db: AsyncSession, account_id: uuid.UUID) -> int:
    """Ata filas sin proyecto a los proyectos de esta cuenta por su slug de texto.
    Las filas sin cuenta (era single-account, pre-v0017) son reclamables; las de otras
    cuentas jamás."""
    mapped = (await db.execute(
        select(Project.id, Project.sentry_project_slug).where(
            Project.account_id == account_id, Project.sentry_project_slug.is_not(None)
        )
    )).all()
    total = 0
    for pid, slug in mapped:
        res = await db.execute(
            update(SentryIssue)
            .where(*_unmatched_filter(account_id), SentryIssue.project == slug)
            .values(project_id=pid, account_id=account_id)
        )
        total += int(getattr(res, "rowcount", 0) or 0)  # CursorResult; stub-safe en CI
    await db.flush()
    return total
