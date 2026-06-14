import hashlib
import hmac
import json
import uuid

import pytest
from httpx import AsyncClient

from app.config import settings


def _sentry_sig(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _gh_sig(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.mark.asyncio
async def test_sentry_no_secret_503(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "sentry_client_secret", "")
    r = await client.post("/webhooks/sentry", json={"id": "1"})
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_sentry_invalid_signature_401(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "sentry_client_secret", "supersecret")
    r = await client.post(
        "/webhooks/sentry", content=b'{"id":"1"}',
        headers={"sentry-hook-signature": "malo"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_sentry_upsert_idempotent(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "sentry_client_secret", "supersecret")
    body = json.dumps({"id": "sentry-xyz-1", "title": "TypeError boom",
                       "project": "api", "level": "error"}).encode()
    sig = _sentry_sig("supersecret", body)
    h = {"sentry-hook-signature": sig, "content-type": "application/json"}
    r1 = await client.post("/webhooks/sentry", content=body, headers=h)
    assert r1.status_code == 200
    assert r1.json()["created"] is True
    r2 = await client.post("/webhooks/sentry", content=body, headers=h)
    assert r2.json()["created"] is False
    assert r2.json()["events_count"] == 2  # idempotente: incrementa, no duplica


@pytest.mark.asyncio
async def test_sentry_lands_in_container_not_backlog(client: AsyncClient, monkeypatch):
    """El error aterriza en sentry_issues (contenedor), NO en el backlog automáticamente."""
    from app.database import get_db
    from app.items.models import Item
    from app.webhooks.models import SentryIssue

    monkeypatch.setattr(settings, "sentry_client_secret", "supersecret")
    sid = f"sentry-{uuid.uuid4().hex[:8]}"
    body = json.dumps({"id": sid, "title": "boom NPE", "project": "efrain-api"}).encode()
    r = await client.post(
        "/webhooks/sentry", content=body,
        headers={"sentry-hook-signature": _sentry_sig("supersecret", body)},
    )
    assert r.status_code == 200
    assert "promoted_item" not in r.json()  # ya no se promueve automáticamente
    async for db in client.app.dependency_overrides[get_db]():
        issue = (await db.execute(
            __import__("sqlalchemy").select(SentryIssue).where(SentryIssue.sentry_issue_id == sid)
        )).scalar_one()
        assert issue.status == "new"
        assert issue.item_id is None  # NO hay ítem de backlog todavía
        n_items = await db.scalar(
            __import__("sqlalchemy").select(__import__("sqlalchemy").func.count()).select_from(Item)
            .where(Item.origen == "sentry", Item.title == "boom NPE")
        )
        assert n_items == 0
        break


@pytest.mark.asyncio
async def test_manual_promote_creates_backlog_item(client: AsyncClient, monkeypatch):
    """Promover manualmente un incidente crea un ítem de backlog con la prioridad elegida."""
    from app.database import get_db
    from app.items.models import Item
    from app.webhooks.models import SentryIssue

    monkeypatch.setattr(settings, "sentry_client_secret", "supersecret")
    sid = f"sentry-{uuid.uuid4().hex[:8]}"
    body = json.dumps({"id": sid, "title": "bug real grave", "project": "efrain-api"}).encode()
    await client.post(
        "/webhooks/sentry", content=body,
        headers={"sentry-hook-signature": _sentry_sig("supersecret", body)},
    )
    # login admin para la UI
    from app.auth.service import create_user
    suffix = uuid.uuid4().hex[:8]
    issue_id = None
    async for db in client.app.dependency_overrides[get_db]():
        await create_user(db, f"inc{suffix}@test.cl", "Inc", "pass", "admin")
        issue = (await db.execute(
            __import__("sqlalchemy").select(SentryIssue).where(SentryIssue.sentry_issue_id == sid)
        )).scalar_one()
        issue_id = str(issue.id)
        break
    login = await client.post(
        "/auth/login", data={"email": f"inc{suffix}@test.cl", "password": "pass"},
        follow_redirects=False,
    )
    cookies = dict(login.cookies)
    r = await client.post(f"/ui/incidentes/{issue_id}/promote", data={"priority": "p0"}, cookies=cookies)
    assert r.status_code == 204
    async for db in client.app.dependency_overrides[get_db]():
        issue = await db.get(SentryIssue, uuid.UUID(issue_id))
        assert issue.status == "linked"
        item = await db.get(Item, issue.item_id)
        assert item.priority == "p0"
        assert item.origen == "sentry"
        break


@pytest.mark.asyncio
async def test_sentry_triage_hides_noise(client: AsyncClient, monkeypatch):
    """El triage marca ruido y auto-oculta el incidente (status=ignored), sin tocar el backlog."""
    from app.ai import llm
    from app.database import get_db
    from app.jobs.handlers import handle_triage_sentry
    from app.webhooks.models import SentryIssue

    monkeypatch.setattr(settings, "sentry_client_secret", "supersecret")
    sid = f"sentry-{uuid.uuid4().hex[:8]}"
    body = json.dumps({"id": sid, "title": "timeout aislado", "project": "efrain-api"}).encode()
    await client.post(
        "/webhooks/sentry", content=body,
        headers={"sentry-hook-signature": _sentry_sig("supersecret", body)},
    )

    async def fake_triage(title, context):
        return {"triage": "ruido"}

    monkeypatch.setattr(llm, "triage_sentry", fake_triage)
    async for db in client.app.dependency_overrides[get_db]():
        issue = (await db.execute(
            __import__("sqlalchemy").select(SentryIssue).where(SentryIssue.sentry_issue_id == sid)
        )).scalar_one()
        out = await handle_triage_sentry(db, issue.id)
        await db.commit()
        assert out["triage"] == "ruido"
        issue2 = await db.get(SentryIssue, issue.id)
        assert issue2.status == "ignored"
        assert issue2.item_id is None
        break


@pytest.mark.asyncio
async def test_github_no_secret_503(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "github_webhook_secret", "")
    r = await client.post("/webhooks/github", json={})
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_github_completes_item(client: AsyncClient, monkeypatch):
    from app.database import get_db
    from app.items.models import Item
    from app.scopes.models import Scope

    monkeypatch.setattr(settings, "github_webhook_secret", "ghsecret")
    async for db in client.app.dependency_overrides[get_db]():
        scope = Scope(name=f"gh-{uuid.uuid4().hex[:8]}")
        db.add(scope)
        await db.flush()
        item = Item(scope_id=scope.id, title="cerrar por commit", type="bug", status="en-curso")
        db.add(item)
        await db.commit()
        await db.refresh(item)
        item_id = str(item.id)
        break

    body = json.dumps({"commits": [
        {"id": "abc123def456", "message": f"fix(auth): resuelto pulso:{item_id}"}
    ]}).encode()
    r = await client.post(
        "/webhooks/github", content=body,
        headers={"x-hub-signature-256": _gh_sig("ghsecret", body), "x-github-event": "push"},
    )
    assert r.status_code == 200
    assert item_id in r.json()["completed"]

    # idempotente: reenvío no recompleta
    r2 = await client.post(
        "/webhooks/github", content=body,
        headers={"x-hub-signature-256": _gh_sig("ghsecret", body), "x-github-event": "push"},
    )
    assert item_id not in r2.json()["completed"]


@pytest.mark.asyncio
async def test_github_invalid_signature_401(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "github_webhook_secret", "ghsecret")
    r = await client.post(
        "/webhooks/github", content=b"{}",
        headers={"x-hub-signature-256": "sha256=malo", "x-github-event": "push"},
    )
    assert r.status_code == 401
