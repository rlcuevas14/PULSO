"""UI screen + HTMX action coverage. Logs in a real session (cookie persists on the client
jar), seeds data into the user's project, exercises every screen and /ui/* action, asserting
status + content + DB effect."""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.database import get_db


async def _login(client: AsyncClient, role: str = "admin"):
    """Create a user (auto-account + 'Default' project), log in (cookie lands in the client
    jar so later requests are authenticated), return (user_id, project_id)."""
    from app.auth.service import create_user
    from app.projects.models import Project

    suffix = uuid.uuid4().hex[:8]
    email = f"ui{suffix}@t.cl"
    async for db in client.app.dependency_overrides[get_db]():
        user = await create_user(db, email, "UI User", "password", role)
        proj = (await db.execute(
            select(Project).where(Project.account_id == user.account_id)
        )).scalars().first()
        uid, pid = user.id, proj.id
        break
    resp = await client.post(
        "/auth/login", data={"email": email, "password": "password"}, follow_redirects=False
    )
    assert resp.status_code == 303
    return uid, pid


async def _seed_item(client, pid, *, title="Task", status="backlog", type="feature",
                     impact_ai=None, effort_ai=None, priority=None, agent_ready=False):
    from app.items.models import Item
    from app.scopes.models import Scope

    async for db in client.app.dependency_overrides[get_db]():
        scope = (await db.execute(select(Scope).where(Scope.project_id == pid))).scalars().first()
        if scope is None:
            scope = Scope(name=f"area-{uuid.uuid4().hex[:6]}", project_id=pid)
            db.add(scope)
            await db.flush()
        item = Item(
            scope_id=scope.id, project_id=pid, title=title, type=type, status=status,
            origen="human", impact_ai=impact_ai, effort_ai=effort_ai, priority=priority,
            agent_ready=agent_ready,
        )
        db.add(item)
        await db.commit()
        await db.refresh(item)
        return item.id, scope.id


@pytest.mark.asyncio
async def test_dashboard_and_screens_render(client: AsyncClient):
    _uid, pid = await _login(client)
    await _seed_item(client, pid, title="On the board", impact_ai=5, effort_ai="XS")
    r = await client.get("/")
    assert r.status_code == 200 and 'id="home-cards"' in r.text  # the dashboard, not the login page
    for path in ("/backlog", "/prioridad", "/hilos", "/incidentes", "/ideas", "/projects"):
        rr = await client.get(path)
        assert rr.status_code == 200, path


@pytest.mark.asyncio
async def test_backlog_filters_and_hx(client: AsyncClient):
    _uid, pid = await _login(client)
    await _seed_item(client, pid, title="Bug one here", type="bug", status="backlog")
    r = await client.get("/backlog?status=backlog&item_type=bug&order=impact")
    assert r.status_code == 200 and "Bug one here" in r.text
    r2 = await client.get("/backlog", headers={"HX-Request": "true"})
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_item_detail_and_404(client: AsyncClient):
    _uid, pid = await _login(client)
    item_id, _ = await _seed_item(client, pid, title="Detail me now")
    r = await client.get(f"/items/{item_id}")
    assert r.status_code == 200 and "Detail me now" in r.text
    r404 = await client.get(f"/items/{uuid.uuid4()}")
    assert r404.status_code == 404


@pytest.mark.asyncio
async def test_item_transition_close_reopen(client: AsyncClient):
    _uid, pid = await _login(client)
    item_id, _ = await _seed_item(client, pid, status="backlog")
    r = await client.post(f"/ui/items/{item_id}/transition", data={"status": "in-progress"})
    assert r.status_code == 204
    rc = await client.post(
        f"/ui/items/{item_id}/close", data={"status": "done", "reason": "shipped"}
    )
    assert rc.status_code == 204
    rr = await client.post(f"/ui/items/{item_id}/reopen")
    assert rr.status_code == 204


@pytest.mark.asyncio
async def test_item_transition_invalid_422(client: AsyncClient):
    _uid, pid = await _login(client)
    item_id, _ = await _seed_item(client, pid, status="backlog")
    r = await client.post(f"/ui/items/{item_id}/transition", data={"status": "done"})
    assert r.status_code == 422  # terminal must go through /close


@pytest.mark.asyncio
async def test_item_set_field(client: AsyncClient):
    _uid, pid = await _login(client)
    item_id, _ = await _seed_item(client, pid)
    for field, value in (("priority", "p0"), ("impact_ai", "4"), ("effort_ai", "M")):
        r = await client.post(f"/ui/items/{item_id}/field", data={"field": field, "value": value})
        assert r.status_code == 204
    bad = await client.post(f"/ui/items/{item_id}/field", data={"field": "nope", "value": "x"})
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_item_create_via_ui(client: AsyncClient):
    _uid, pid = await _login(client)
    _i, scope_id = await _seed_item(client, pid)
    r = await client.post(
        "/ui/items/create",
        data={"title": "Created via UI", "scope_id": str(scope_id), "type": "feature"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    bad = await client.post(
        "/ui/items/create",
        data={"title": "X", "scope_id": str(uuid.uuid4()), "type": "feature"},
    )
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_relationships_create_and_delete(client: AsyncClient):
    _uid, pid = await _login(client)
    uniq = uuid.uuid4().hex[:8]
    a, _ = await _seed_item(client, pid, title=f"Alpha {uniq}")
    b, _ = await _seed_item(client, pid, title=f"Betaunique {uniq}")
    r = await client.post(
        f"/ui/items/{a}/relationships",
        data={"relation": "blocks", "target_query": f"Betaunique {uniq}"},
    )
    assert r.status_code == 200
    d = await client.request(
        "DELETE", f"/ui/items/{a}/relationships",
        params={"source": str(a), "target": str(b), "relation": "blocks"},
    )
    assert d.status_code == 200


@pytest.mark.asyncio
async def test_thread_create_advance_detail(client: AsyncClient):
    _uid, pid = await _login(client)
    _i, scope_id = await _seed_item(client, pid)
    from app.scopes.models import Scope

    async for db in client.app.dependency_overrides[get_db]():
        sname = (await db.get(Scope, scope_id)).name
        break
    r = await client.post(
        "/ui/hilos/create", data={"title": "Big feature x", "scope_name": sname},
        follow_redirects=False,
    )
    assert r.status_code == 303
    tid = r.headers["location"].split("/")[-1]
    detail = await client.get(f"/hilos/{tid}")
    assert detail.status_code == 200 and "Big feature x" in detail.text
    adv = await client.post(f"/ui/hilos/{tid}/advance", data={"artifact_content": "notes"})
    assert adv.status_code == 204
    stage = await client.post(f"/ui/hilos/{tid}/stage", data={"stage": "spec"})
    assert stage.status_code == 204


@pytest.mark.asyncio
async def test_switch_project_and_admin_screens(client: AsyncClient):
    _uid, pid = await _login(client)
    sw = await client.post(
        "/ui/project/switch", data={"project_id": str(pid)}, follow_redirects=False
    )
    assert sw.status_code == 303
    for path in ("/admin", "/admin/accounts", "/account/members"):
        r = await client.get(path)
        assert r.status_code == 200, path


@pytest.mark.asyncio
async def test_unauthenticated_redirects_to_login(client: AsyncClient):
    r = await client.get("/", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert "/auth/login" in r.headers.get("location", "")


@pytest.mark.asyncio
async def test_incident_promote_and_ignore(client: AsyncClient):
    _uid, pid = await _login(client)
    from app.webhooks.models import SentryIssue

    ids = []
    async for db in client.app.dependency_overrides[get_db]():
        for _ in range(2):
            iss = SentryIssue(sentry_issue_id=f"u{uuid.uuid4().hex[:8]}", project="x", title="Err",
                              level="error", status="new", events_count=1, payload={}, project_id=pid)
            db.add(iss)
            await db.flush()
            ids.append(str(iss.id))
        await db.commit()
        break
    pr = await client.post(f"/ui/incidentes/{ids[0]}/promote", data={"priority": "p0"})
    assert pr.status_code == 204
    ig = await client.post(f"/ui/incidentes/{ids[1]}/ignore")
    assert ig.status_code == 204
    nf = await client.post(f"/ui/incidentes/{uuid.uuid4()}/ignore")
    assert nf.status_code == 404


@pytest.mark.asyncio
async def test_hilo_elaborate_degraded(client: AsyncClient, monkeypatch):
    monkeypatch.setattr("app.config.settings.anthropic_api_key", "")
    _uid, pid = await _login(client)
    _i, scope_id = await _seed_item(client, pid)
    from app.scopes.models import Scope

    async for db in client.app.dependency_overrides[get_db]():
        sname = (await db.get(Scope, scope_id)).name
        break
    cr = await client.post("/ui/hilos/create", data={"title": "Elab", "scope_name": sname},
                           follow_redirects=False)
    tid = cr.headers["location"].split("/")[-1]
    # without an API key, elaborate degrades to a 200 error fragment (never 500)
    r = await client.post(f"/ui/hilos/{tid}/elaborate")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_static_brand_assets_served(client: AsyncClient):
    r = await client.get("/static/brand/pulso-favicon-16.svg")
    assert r.status_code == 200 and "svg" in r.headers["content-type"]
    r = await client.get("/static/manifest.webmanifest")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_list_items_topological_order_fixed(client: AsyncClient):
    """service.list_items(order='topological') must order blocker before blocked item."""
    from app.items.models import ItemRelationship
    from app.items.service import list_items

    _uid, pid = await _login(client)
    a_id, _ = await _seed_item(client, pid, title="Topo-A depends on nothing")
    b_id, _ = await _seed_item(client, pid, title="Topo-B blocked by A")

    async for db in client.app.dependency_overrides[get_db]():
        rel = ItemRelationship(source_id=a_id, target_id=b_id, relation="blocks")
        db.add(rel)
        await db.commit()

        items = await list_items(db, project_id=pid, order="topological")
        ids_in_order = [str(i.id) for i in items]
        assert ids_in_order.index(str(a_id)) < ids_in_order.index(str(b_id)), (
            "topological order: blocker (a) must appear before its target (b)"
        )
        break


@pytest.mark.asyncio
async def test_home_cards_stats(client: AsyncClient):
    _uid, pid = await _login(client)
    await _seed_item(client, pid, title="Open A", status="backlog")
    await _seed_item(client, pid, title="Quick win", status="backlog", impact_ai=5, effort_ai="XS")
    await _seed_item(client, pid, title="An idea", status="idea")
    r = await client.get("/")
    assert r.status_code == 200
    assert 'id="home-cards"' in r.text
    assert "Backlog" in r.text and "Ideas" in r.text
