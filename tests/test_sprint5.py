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
    r = await client.post("/api/v1/webhooks/sentry", json={"id": "1"})
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_sentry_invalid_signature_401(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "sentry_client_secret", "supersecret")
    r = await client.post(
        "/api/v1/webhooks/sentry", content=b'{"id":"1"}',
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
    r1 = await client.post("/api/v1/webhooks/sentry", content=body, headers=h)
    assert r1.status_code == 200
    assert r1.json()["created"] is True
    r2 = await client.post("/api/v1/webhooks/sentry", content=body, headers=h)
    assert r2.json()["created"] is False
    assert r2.json()["events_count"] == 2  # idempotente: incrementa, no duplica


@pytest.mark.asyncio
async def test_sentry_promotes_bug_real(client: AsyncClient, monkeypatch):
    from app.database import get_db
    from app.webhooks.models import SentryIssue

    monkeypatch.setattr(settings, "sentry_client_secret", "supersecret")
    sid = f"sentry-{uuid.uuid4().hex[:8]}"
    async for db in client.app.dependency_overrides[get_db]():
        db.add(SentryIssue(sentry_issue_id=sid, project="api", title="bug grave",
                           level="error", status="new", triage="bug-real", events_count=1,
                           payload={"sanitized_title": "bug grave"}))
        await db.commit()
        break
    body = json.dumps({"id": sid, "title": "bug grave", "project": "api"}).encode()
    r = await client.post(
        "/api/v1/webhooks/sentry", content=body,
        headers={"sentry-hook-signature": _sentry_sig("supersecret", body)},
    )
    assert r.json()["promoted_item"] is not None


@pytest.mark.asyncio
async def test_github_no_secret_503(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "github_webhook_secret", "")
    r = await client.post("/api/v1/webhooks/github", json={})
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
        "/api/v1/webhooks/github", content=body,
        headers={"x-hub-signature-256": _gh_sig("ghsecret", body), "x-github-event": "push"},
    )
    assert r.status_code == 200
    assert item_id in r.json()["completed"]

    # idempotente: reenvío no recompleta
    r2 = await client.post(
        "/api/v1/webhooks/github", content=body,
        headers={"x-hub-signature-256": _gh_sig("ghsecret", body), "x-github-event": "push"},
    )
    assert item_id not in r2.json()["completed"]


@pytest.mark.asyncio
async def test_github_invalid_signature_401(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "github_webhook_secret", "ghsecret")
    r = await client.post(
        "/api/v1/webhooks/github", content=b"{}",
        headers={"x-hub-signature-256": "sha256=malo", "x-github-event": "push"},
    )
    assert r.status_code == 401
