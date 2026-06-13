import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.ai import llm
from app.items.models import AiEnrichment, Item
from app.jobs.handlers import handle_enrich
from app.scopes.models import Scope


async def _make_item(test_engine, **kw):
    TestSession = async_sessionmaker(test_engine, expire_on_commit=False)
    async with TestSession() as db:
        scope = Scope(name=f"s1-{uuid.uuid4().hex[:8]}")
        db.add(scope)
        await db.flush()
        item = Item(scope_id=scope.id, title=kw.get("title", "Mejorar login"),
                    type="feature", status="backlog", summary_md="Detalle.")
        db.add(item)
        await db.commit()
        await db.refresh(item)
        return item.id


@pytest.mark.asyncio
async def test_enrich_updates_item(test_engine, monkeypatch):
    item_id = await _make_item(test_engine)

    async def fake_enrich(title, summary, item_type):
        return {"impact": 5, "effort": "S", "rationale": "Afecta a todos los usuarios.",
                "tokens_in": 100, "tokens_out": 20, "model": "claude-haiku-4-5-20251001"}

    async def fake_embed(content):
        return None  # sin pgvector local

    monkeypatch.setattr(llm, "enrich_item", fake_enrich)
    monkeypatch.setattr(llm, "embed_text", fake_embed)

    TestSession = async_sessionmaker(test_engine, expire_on_commit=False)
    async with TestSession() as db:
        out = await handle_enrich(db, item_id)
        await db.commit()
        assert out["enriched"] is True
        item = (await db.execute(select(Item).where(Item.id == item_id))).scalar_one()
        assert item.impact_ai == 5
        assert item.effort_ai == "S"
        enr = (await db.execute(select(AiEnrichment).where(AiEnrichment.item_id == item_id))).scalars().all()
        assert len(enr) == 1


@pytest.mark.asyncio
async def test_enrich_graceful_without_key(test_engine, monkeypatch):
    item_id = await _make_item(test_engine, title="Sin key")

    async def raise_unavailable(title, summary, item_type):
        raise llm.LLMUnavailable("no key")

    async def fake_embed(content):
        return None

    monkeypatch.setattr(llm, "enrich_item", raise_unavailable)
    monkeypatch.setattr(llm, "embed_text", fake_embed)

    TestSession = async_sessionmaker(test_engine, expire_on_commit=False)
    async with TestSession() as db:
        out = await handle_enrich(db, item_id)
        await db.commit()
        assert out["enriched"] is False  # degrada, no rompe


def test_clamp_helpers():
    assert llm._clamp_impact(7) == 5
    assert llm._clamp_impact(0) == 1
    assert llm._clamp_impact("x") is None
    assert llm._clamp_effort("s") == "S"
    assert llm._clamp_effort("ZZ") is None


def test_extract_json():
    assert llm._extract_json('prefijo {"impact": 4} sufijo') == {"impact": 4}
    assert llm._extract_json("sin json") == {}
