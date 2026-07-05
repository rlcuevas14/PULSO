"""Session-flash: celebración pop-once en completar; toast verde en acciones."""
import pytest
from httpx import AsyncClient

from tests.test_ui import _login, _seed_item


@pytest.mark.asyncio
async def test_close_done_celebrates_once(client: AsyncClient):
    _uid, pid = await _login(client)
    item_id, _sid = await _seed_item(client, pid, title="Ship it", status="in-review")
    r = await client.post(f"/ui/items/{item_id}/close", data={"status": "done", "reason": "ok"})
    assert r.status_code == 204
    r1 = await client.get("/")
    assert "¡Completado!" in r1.text and "Ship it" in r1.text
    r2 = await client.get("/")          # pop-once: nunca se repite al refrescar
    assert "¡Completado!" not in r2.text


@pytest.mark.asyncio
async def test_close_discarded_toasts_not_celebrates(client: AsyncClient):
    _uid, pid = await _login(client)
    item_id, _sid = await _seed_item(client, pid, title="Nope", status="backlog")
    r = await client.post(f"/ui/items/{item_id}/close", data={"status": "discarded", "reason": "no"})
    assert r.status_code == 204
    r1 = await client.get("/")
    # el mensaje pasa por |tojson (ensure_ascii): "Ítem" llega como "Ítem" — se
    # asserta el fragmento ASCII del mensaje.
    assert "¡Completado!" not in r1.text and "tem descartado" in r1.text
