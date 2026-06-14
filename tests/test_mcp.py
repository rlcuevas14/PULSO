import uuid

import pytest
from httpx import AsyncClient


async def _token(client: AsyncClient, scopes: str = "write") -> str:
    from app.auth.service import create_api_token, create_user
    from app.database import get_db

    suffix = uuid.uuid4().hex[:8]
    async for db in client.app.dependency_overrides[get_db]():
        user = await create_user(db, f"mcp{suffix}@test.cl", "MCP", "pass", "admin")
        _tok, raw = await create_api_token(db, f"mcp-{suffix}", scopes, user.id)
        break
    return raw


def _hdr(raw: str) -> dict:
    return {"Authorization": f"Bearer {raw}"}


async def _rpc(client, raw, method, params=None, rpc_id=1):
    return await client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params or {}},
        headers=_hdr(raw),
    )


@pytest.mark.asyncio
async def test_initialize_handshake(client: AsyncClient):
    raw = await _token(client)
    r = await _rpc(client, raw, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}})
    assert r.status_code == 200
    res = r.json()["result"]
    assert res["protocolVersion"] == "2025-03-26"
    assert "tools" in res["capabilities"]
    assert res["serverInfo"]["name"] == "pulso"


@pytest.mark.asyncio
async def test_no_token_401(client: AsyncClient):
    r = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_invalid_token_401(client: AsyncClient):
    r = await client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        headers=_hdr("token-falso-xyz"),
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_tools_list(client: AsyncClient):
    raw = await _token(client)
    r = await _rpc(client, raw, "tools/list")
    names = [t["name"] for t in r.json()["result"]["tools"]]
    assert "pulso_contexto" in names
    assert "pulso_crear" in names
    assert "pulso_completar" in names


@pytest.mark.asyncio
async def test_notification_returns_202(client: AsyncClient):
    raw = await _token(client)
    r = await client.post(
        "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=_hdr(raw),
    )
    assert r.status_code == 202


@pytest.mark.asyncio
async def test_get_mcp_405(client: AsyncClient):
    r = await client.get("/mcp")
    assert r.status_code == 405


@pytest.mark.asyncio
async def test_pulso_crear_and_buscar(client: AsyncClient):
    raw = await _token(client, "write")
    r = await _rpc(client, raw, "tools/call", {
        "name": "pulso_crear",
        "arguments": {"title": "Tarea MCP zzz", "type": "feature", "scope_name": "mcp-scope"},
    })
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["isError"] is False
    # buscar la encuentra
    s = await _rpc(client, raw, "tools/call", {"name": "pulso_buscar", "arguments": {"q": "MCP zzz"}})
    import json
    found = json.loads(s.json()["result"]["content"][0]["text"])
    assert any("MCP zzz" in i["title"] for i in found)


@pytest.mark.asyncio
async def test_read_token_cannot_write(client: AsyncClient):
    raw = await _token(client, "read")
    r = await _rpc(client, raw, "tools/call", {
        "name": "pulso_crear",
        "arguments": {"title": "no debería crearse", "type": "bug", "scope_name": "x"},
    })
    assert r.status_code == 200
    assert r.json()["result"]["isError"] is True
    assert "write" in r.json()["result"]["content"][0]["text"]


@pytest.mark.asyncio
async def test_pulso_completar_ambiguous_aborts(client: AsyncClient):
    raw = await _token(client, "write")
    # Dos ítems con el MISMO título → empate de rank → ambiguo.
    for _ in range(2):
        await _rpc(client, raw, "tools/call", {
            "name": "pulso_crear",
            "arguments": {"title": "titulo identico ambiguo", "type": "feature", "scope_name": "amb"},
        })
    r = await _rpc(client, raw, "tools/call", {
        "name": "pulso_completar", "arguments": {"search_query": "titulo identico ambiguo"},
    })
    assert r.json()["result"]["isError"] is True
    assert "ambig" in r.json()["result"]["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_pulso_contexto_runs(client: AsyncClient):
    raw = await _token(client)
    r = await _rpc(client, raw, "tools/call", {"name": "pulso_contexto", "arguments": {}})
    import json
    ctx = json.loads(r.json()["result"]["content"][0]["text"])
    assert "local" in ctx
    assert "neighborhood" in ctx
    assert ctx["semantic"] is None  # sin embeddings


@pytest.mark.asyncio
async def test_method_not_found(client: AsyncClient):
    raw = await _token(client)
    r = await _rpc(client, raw, "no/existe")
    assert r.json()["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_scope_tools(client: AsyncClient):
    import json as _json

    from app.database import get_db
    from app.scopes.models import Scope

    raw = await _token(client, "write")
    sname = f"curric-{uuid.uuid4().hex[:6]}"
    async for db in client.app.dependency_overrides[get_db]():
        db.add(Scope(name=sname, description="Currículo y OAs"))
        await db.commit()
        break

    # 1) pulso_scopes lista con nombre + descripción
    sc = await _rpc(client, raw, "tools/call", {"name": "pulso_scopes", "arguments": {}})
    scopes = _json.loads(sc.json()["result"]["content"][0]["text"])
    assert any(s["name"] == sname and s["description"] == "Currículo y OAs" for s in scopes)

    # 2) crear con variante de mayúsculas/espacios → matchea el existente, NO duplica
    cr = await _rpc(client, raw, "tools/call", {
        "name": "pulso_crear",
        "arguments": {"title": "OA electivas", "type": "feature", "scope_name": f"  {sname.upper()}  "},
    })
    created = _json.loads(cr.json()["result"]["content"][0]["text"])
    assert created["scope"] == sname  # devuelve el nombre del scope, no solo el id
    async for db in client.app.dependency_overrides[get_db]():
        n = await db.scalar(
            __import__("sqlalchemy").select(__import__("sqlalchemy").func.count())
            .select_from(Scope).where(__import__("sqlalchemy").func.lower(Scope.name) == sname.lower())
        )
        assert n == 1  # no se creó un duplicado por la variante de caso
        break

    # 3) mover a otro scope existente
    other = f"otro-{uuid.uuid4().hex[:6]}"
    async for db in client.app.dependency_overrides[get_db]():
        db.add(Scope(name=other))
        await db.commit()
        break
    mv = await _rpc(client, raw, "tools/call", {
        "name": "pulso_mover_scope",
        "arguments": {"item_id": created["id"], "scope_name": other},
    })
    moved = _json.loads(mv.json()["result"]["content"][0]["text"])
    assert moved["scope"] == other


@pytest.mark.asyncio
async def test_incident_tools_flow(client: AsyncClient, monkeypatch):
    import json as _json

    from app.database import get_db
    from app.webhooks import service as wservice
    from app.webhooks.models import SentryIssue

    raw = await _token(client, "write")
    sid = f"inc-{uuid.uuid4().hex[:8]}"
    issue_id = None
    async for db in client.app.dependency_overrides[get_db]():
        issue = SentryIssue(sentry_issue_id=sid, project="python-fastapi",
                            title="KeyError en /api/x", level="error", status="new",
                            events_count=5, payload={"web_url": "https://sentry.io/i/1"})
        db.add(issue)
        await db.commit()
        await db.refresh(issue)
        issue_id = str(issue.id)
        break

    # 1) listar incidentes
    li = await _rpc(client, raw, "tools/call", {"name": "pulso_incidentes", "arguments": {}})
    listed = _json.loads(li.json()["result"]["content"][0]["text"])
    assert any(i["id"] == issue_id for i in listed)

    # 2) detalle con stack trace (Sentry mockeado → no gasta nada)
    async def fake_detail(sentry_id):
        return {"title": "KeyError", "culprit": "app.api.x",
                "stacktrace": "KeyError: 'foo'\n  app/api/x.py:42 in handler"}

    monkeypatch.setattr(wservice, "fetch_issue_detail", fake_detail)
    det = await _rpc(client, raw, "tools/call", {"name": "pulso_incidente", "arguments": {"id": issue_id}})
    detail = _json.loads(det.json()["result"]["content"][0]["text"])
    assert "x.py:42" in detail["stacktrace"]

    # 3) resolver (sin tocar Sentry)
    res = await _rpc(client, raw, "tools/call", {
        "name": "pulso_incidente_resolver",
        "arguments": {"id": issue_id, "nota": "arreglado", "resolver_en_sentry": False},
    })
    out = _json.loads(res.json()["result"]["content"][0]["text"])
    assert out["status"] == "resolved"
    async for db in client.app.dependency_overrides[get_db]():
        issue = await db.get(SentryIssue, uuid.UUID(issue_id))
        assert issue.status == "resolved"
        break
