import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.items import graph
from app.items.graph import topological_order
from app.items.models import Item, ItemRelationship
from app.scopes.models import Scope

# ---------- Kahn topological_order (pure) ----------

def test_topo_simple_chain():
    # A blocks B, B blocks C  => orden A, B, C
    r = topological_order(["A", "B", "C"], [("A", "B", "blocks"), ("B", "C", "blocks")])
    assert r["has_cycle"] is False
    assert r["order"].index("A") < r["order"].index("B") < r["order"].index("C")


def test_topo_requires_inverts_direction():
    # A requires B  => B antes que A
    r = topological_order(["A", "B"], [("A", "B", "requires")])
    assert r["order"].index("B") < r["order"].index("A")


def test_topo_cycle_does_not_lose_items():
    # A blocks B, B blocks A  => ciclo; ambos deben aparecer, flag has_cycle.
    r = topological_order(["A", "B"], [("A", "B", "blocks"), ("B", "A", "blocks")])
    assert r["has_cycle"] is True
    assert set(r["order"]) == {"A", "B"}
    assert len(r["order"]) == 2  # invariante: no se pierde ningún ítem


def test_topo_ignores_non_precedence_relations():
    r = topological_order(["A", "B"], [("A", "B", "conflicts"), ("A", "B", "related")])
    assert r["has_cycle"] is False
    assert set(r["order"]) == {"A", "B"}


def test_topo_empty():
    r = topological_order([], [])
    assert r["order"] == []
    assert r["has_cycle"] is False


# ---------- Graph queries (DB) ----------

async def _make_items(db, n: int, prefix: str):
    scope = Scope(name=f"graph-{prefix}-{uuid.uuid4().hex[:8]}")
    db.add(scope)
    await db.flush()
    items = []
    for i in range(n):
        it = Item(scope_id=scope.id, title=f"{prefix}-{i}", type="feature", status="backlog")
        db.add(it)
        items.append(it)
    await db.flush()
    return scope, items


@pytest.mark.asyncio
async def test_neighborhood_zero_relations(test_engine):
    TestSession = async_sessionmaker(test_engine, expire_on_commit=False)
    async with TestSession() as db:
        scope, items = await _make_items(db, 2, "nbz")
        await db.commit()
        result = await graph.neighborhood(db, scope.id)
        # Sin arcos: solo la semilla (depth 0).
        ids = {r["id"] for r in result}
        assert str(items[0].id) in ids
        assert all(r["depth"] == 0 for r in result)


@pytest.mark.asyncio
async def test_neighborhood_reaches_other_scope(test_engine):
    TestSession = async_sessionmaker(test_engine, expire_on_commit=False)
    async with TestSession() as db:
        scope, items = await _make_items(db, 1, "nba")
        # Un ítem en OTRO scope que bloquea al de la semilla.
        other_scope = Scope(name=f"graph-other-{uuid.uuid4().hex[:8]}")
        db.add(other_scope)
        await db.flush()
        blocker = Item(scope_id=other_scope.id, title="blocker", type="bug", status="backlog")
        db.add(blocker)
        await db.flush()
        db.add(ItemRelationship(source_id=blocker.id, target_id=items[0].id, relation="blocks"))
        await db.commit()
        result = await graph.neighborhood(db, scope.id)
        ids = {r["id"] for r in result}
        assert str(blocker.id) in ids  # alcanzado por vecindad aunque sea de otro scope


@pytest.mark.asyncio
async def test_blockers_and_unblocked_by(test_engine):
    TestSession = async_sessionmaker(test_engine, expire_on_commit=False)
    async with TestSession() as db:
        scope, items = await _make_items(db, 2, "blk")
        a, b = items
        db.add(ItemRelationship(source_id=a.id, target_id=b.id, relation="blocks"))
        await db.commit()
        # b está bloqueado por a (abierto).
        blockers = await graph.blockers_of(db, b.id)
        assert any(x["id"] == str(a.id) for x in blockers)
        # cerrar a => b queda desbloqueado.
        a.status = "done"
        await db.commit()
        unblocked = await graph.unblocked_by(db, a.id)
        assert any(x["id"] == str(b.id) for x in unblocked)


@pytest.mark.asyncio
async def test_conflicts_symmetric_visible_both_directions(test_engine):
    TestSession = async_sessionmaker(test_engine, expire_on_commit=False)
    async with TestSession() as db:
        scope, items = await _make_items(db, 2, "cf")
        a, b = items
        db.add(ItemRelationship(source_id=a.id, target_id=b.id, relation="conflicts"))
        await db.commit()
        # La vecindad desde el scope alcanza ambos en cualquier sentido.
        result = await graph.neighborhood(db, scope.id)
        ids = {r["id"] for r in result}
        assert str(a.id) in ids and str(b.id) in ids
