"""Regresión: el form #filters serializa strings vacíos para los bool (contrato HTML).

El navegador manda ?stale=&graph_blocked=&quickwins=&urgent=&agent_ready= en CADA
interacción (hx-include="#filters" incluye los hidden inputs vacíos). El server debe
tratar "" como ausente — bug reportado 2026-07-10: BOARD/CLOSED devolvían 422.
"""
import uuid

import pytest
from httpx import AsyncClient

from app.database import get_db

# La query EXACTA que arma el navegador con todos los filtros apagados.
_EMPTY_BOOLS = ("stale=&graph_blocked=&quickwins=&urgent=&agent_ready="
                "&scope=&status=&item_type=&origen=&priority=&effort=&q=&group=&order=priority")


async def _login_owner(client: AsyncClient):
    from app.auth.service import create_user
    s = uuid.uuid4().hex[:8]
    email = f"bf{s}@t.cl"
    async for db in client.app.dependency_overrides[get_db]():
        user = await create_user(db, email, "O", "password", "admin")
        account_id = user.account_id
        break
    r = await client.post("/auth/login", data={"email": email, "password": "password"},
                          follow_redirects=False)
    assert r.status_code == 303
    return account_id


@pytest.mark.asyncio
async def test_board_button_with_browser_query(client: AsyncClient):
    await _login_owner(client)
    r = await client.get(f"/backlog?view=board&show=open&{_EMPTY_BOOLS}",
                         headers={"HX-Request": "true"})
    assert r.status_code == 200, r.text[:300]


@pytest.mark.asyncio
async def test_closed_button_with_browser_query(client: AsyncClient):
    await _login_owner(client)
    r = await client.get(f"/backlog?view=list&show=closed&{_EMPTY_BOOLS}",
                         headers={"HX-Request": "true"})
    assert r.status_code == 200, r.text[:300]


@pytest.mark.asyncio
async def test_chip_on_sends_true_others_empty(client: AsyncClient):
    await _login_owner(client)
    q = _EMPTY_BOOLS.replace("quickwins=", "quickwins=true")
    r = await client.get(f"/backlog?view=list&show=open&{q}",
                         headers={"HX-Request": "true"})
    assert r.status_code == 200, r.text[:300]


@pytest.mark.asyncio
async def test_board_move_with_empty_form_bools(client: AsyncClient):
    """El drag&drop postea el mismo form: bools vacíos no deben romper el 200-siempre."""
    from app.items.models import Item
    from app.projects.models import Project
    from app.scopes.models import Scope
    from sqlalchemy import select

    account_id = await _login_owner(client)
    async for db in client.app.dependency_overrides[get_db]():
        proj = (await db.execute(
            select(Project).where(Project.account_id == account_id)
        )).scalars().first()
        sc = Scope(name=f"qa-{uuid.uuid4().hex[:6]}", project_id=proj.id)
        db.add(sc)
        await db.flush()
        it = Item(scope_id=sc.id, project_id=proj.id, title="move me", type="feature",
                  status="backlog", origen="human")
        db.add(it)
        await db.commit()
        await db.refresh(it)
        iid = it.id
        break
    r = await client.post(f"/ui/items/{iid}/board-move", data={
        "status": "in-progress", "stale": "", "graph_blocked": "",
        "quickwins": "", "urgent": "", "agent_ready": "",
        "scope": "", "item_type": "", "origen": "", "priority": "", "effort": "",
        "q": "", "order": "priority",
    })
    assert r.status_code == 200, r.text[:300]
