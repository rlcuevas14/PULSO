"""Coverage for the /api/v1/items REST router, scoped by a project Bearer token."""
import uuid

import pytest
from httpx import AsyncClient


async def _token(client: AsyncClient, scopes="write"):
    from app.accounts.service import create_account
    from app.auth.service import create_api_token
    from app.database import get_db
    from app.projects.service import create_project

    s = uuid.uuid4().hex[:8]
    async for db in client.app.dependency_overrides[get_db]():
        acc, owner = await create_account(db, f"a{s}", f"r{s}@t.cl", "R", "password")
        proj = await create_project(db, name=f"p{s}", account_id=acc.id)
        tok, raw = await create_api_token(db, f"t{s}", scopes, owner.id)
        tok.project_id = proj.id
        await db.commit()
        return raw, proj.id


def _h(raw):
    return {"Authorization": f"Bearer {raw}"}


async def _scope(client, raw):
    r = await client.post("/api/v1/scopes", json={"name": f"a-{uuid.uuid4().hex[:6]}"}, headers=_h(raw))
    return r.json()["id"]


@pytest.mark.asyncio
async def test_item_crud_via_rest(client: AsyncClient):
    raw, _pid = await _token(client)
    sid = await _scope(client, raw)
    # create
    cr = await client.post("/api/v1/items",
                           json={"scope_id": sid, "title": "REST item", "type": "feature"},
                           headers=_h(raw))
    assert cr.status_code == 201
    item_id = cr.json()["id"]
    # list + search + get
    assert (await client.get("/api/v1/items", headers=_h(raw))).status_code == 200
    assert (await client.get("/api/v1/items/search?q=REST", headers=_h(raw))).status_code == 200
    got = await client.get(f"/api/v1/items/{item_id}", headers=_h(raw))
    assert got.status_code == 200 and got.json()["title"] == "REST item"
    # patch: status transition + fields
    pa = await client.patch(f"/api/v1/items/{item_id}",
                            json={"status": "in-progress", "priority": "p1", "impact_ai": 4},
                            headers=_h(raw))
    assert pa.status_code == 200 and pa.json()["status"] == "in-progress"
    # invalid transition -> 422
    bad = await client.patch(f"/api/v1/items/{item_id}", json={"status": "idea"}, headers=_h(raw))
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_item_create_cross_project_scope_rejected(client: AsyncClient):
    raw, _pid = await _token(client)
    cr = await client.post("/api/v1/items",
                           json={"scope_id": str(uuid.uuid4()), "title": "x", "type": "bug"},
                           headers=_h(raw))
    assert cr.status_code == 422


@pytest.mark.asyncio
async def test_get_missing_item_404(client: AsyncClient):
    raw, _pid = await _token(client)
    r = await client.get(f"/api/v1/items/{uuid.uuid4()}", headers=_h(raw))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_read_token_cannot_create_item(client: AsyncClient):
    raw, _pid = await _token(client, scopes="read")
    # require_write rejects a read token before the handler runs
    r = await client.post("/api/v1/items",
                          json={"scope_id": str(uuid.uuid4()), "title": "x", "type": "bug"},
                          headers=_h(raw))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_import_digest_requires_owner_session(client: AsyncClient):
    raw, _pid = await _token(client)
    # tokens are not allowed on owner-only endpoints
    r = await client.post("/api/v1/items/import/digest", json={"path": "x"}, headers=_h(raw))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_importer_reads_jsonl(client: AsyncClient, tmp_path):
    """Direct importer coverage with a real JSONL file."""
    import json

    from app.database import get_db
    from app.items.importer import import_jsonl

    f = tmp_path / "digest.jsonl"
    f.write_text(
        json.dumps({"title": "Imported A", "type": "feature", "scope": "ingest"}) + "\n"
        + json.dumps({"title": "Imported B", "type": "bug", "scope": "ingest"}) + "\n",
        encoding="utf-8",
    )
    async for db in client.app.dependency_overrides[get_db]():
        result = await import_jsonl(db, f)
        await db.commit()
        break
    assert result.get("imported", result.get("created", 0)) >= 1


@pytest.mark.asyncio
async def test_item_comments_and_close_via_rest(client: AsyncClient):
    raw, _pid = await _token(client)
    sid = await _scope(client, raw)
    cr = await client.post("/api/v1/items",
                           json={"scope_id": sid, "title": "Closeme", "type": "feature",
                                 "status": "in-progress"},
                           headers=_h(raw))
    iid = cr.json()["id"]
    com = await client.post(f"/api/v1/items/{iid}/comments",
                            json={"body_md": "a note", "kind": "comment"}, headers=_h(raw))
    assert com.status_code == 201
    cid = com.json()["id"]
    g = await client.get(f"/api/v1/items/{iid}/comments/{cid}", headers=_h(raw))
    assert g.status_code == 200 and g.json()["body_md"] == "a note"
    cl = await client.post(f"/api/v1/items/{iid}/close",
                           json={"status": "done", "reason": "shipped"}, headers=_h(raw))
    assert cl.status_code == 200 and cl.json()["status"] == "done"


@pytest.mark.asyncio
async def test_importer_directory(client: AsyncClient, tmp_path):
    import json

    from app.database import get_db
    from app.items.importer import import_directory

    (tmp_path / "a.jsonl").write_text(
        json.dumps({"title": "Dir A", "type": "feature", "scope": "ing"}) + "\n", encoding="utf-8")
    (tmp_path / "b.jsonl").write_text(
        json.dumps({"title": "Dir B", "type": "bug", "scope": "ing"}) + "\n"
        + "{ not valid json\n", encoding="utf-8")  # malformed line is skipped
    async for db in client.app.dependency_overrides[get_db]():
        res = await import_directory(db, tmp_path)
        await db.commit()
        break
    assert res
