"""Lógica de mutación de ítems, compartida por la API JSON, la UI y el MCP.

Centraliza la validación de transiciones (lifecycle) y la auditoría (ItemEvent),
de modo que UI / REST / MCP nunca diverjan.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Select, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import TERMINAL
from app.items import graph
from app.items.lifecycle import valid_transition
from app.items.models import Item, ItemEvent
from app.scopes.models import Scope

# Rango de prioridad humana para el orden "prioridad" (p0 primero, sin prioridad al final).
_PRIORITY_RANK: dict[str | None, int] = {"p0": 0, "p1": 1, "p2": 2, "p3": 3, None: 9}


class TransitionError(ValueError):
    """Transición de estado inválida."""


async def get_item(db: AsyncSession, item_id: uuid.UUID) -> Item | None:
    result = await db.execute(select(Item).where(Item.id == item_id))
    return result.scalar_one_or_none()


async def apply_transition(db: AsyncSession, item: Item, to_status: str, actor: str) -> Item:
    """Cambia el status validando la transición. Las transiciones a estados terminales
    deben pasar por close_item (piden motivo); aquí se rechazan."""
    if to_status in TERMINAL:
        raise TransitionError(
            f"'{to_status}' es terminal — usa cerrar/descartar (con motivo), no un cambio directo."
        )
    if not valid_transition(item.status, to_status):
        raise TransitionError(f"Transición inválida: {item.status} → {to_status}")
    old = item.status
    if old != to_status:
        item.status = to_status
        await db.flush()
        db.add(ItemEvent(
            item_id=item.id, actor=actor, action="status_changed",
            payload={"from": old, "to": to_status},
        ))
    return item


def _merge_source_ref(item: Item, key: str, value: Any) -> None:
    refs = dict(item.source_refs) if isinstance(item.source_refs, dict) else {}
    refs[key] = value
    item.source_refs = refs


async def close_item(
    db: AsyncSession,
    item: Item,
    status: str,
    reason: str | None,
    actor: str,
    commit_sha: str | None = None,
) -> list[dict[str, Any]]:
    """Cierra un ítem (hecho|descartado). Devuelve la lista de ítems que quedaron
    desbloqueados por este cierre (bloqueo derivado del grafo)."""
    if status not in TERMINAL:
        raise TransitionError("status debe ser 'hecho' o 'descartado'")
    if not valid_transition(item.status, status):
        raise TransitionError(f"Transición inválida: {item.status} → {status}")

    item.status = status
    item.closed_at = datetime.now(timezone.utc)
    if commit_sha:
        _merge_source_ref(item, "commit_sha", commit_sha)
    db.add(ItemEvent(
        item_id=item.id, actor=actor, action="closed",
        payload={"status": status, "reason": reason, "commit_sha": commit_sha},
    ))
    await db.flush()

    # El bloqueo es derivado: tras cerrar, calcular qué targets quedaron sin bloqueador abierto.
    unblocked = await graph.unblocked_by(db, item.id)
    for t in unblocked:
        db.add(ItemEvent(
            item_id=uuid.UUID(t["id"]), actor=actor, action="unblocked_by",
            payload={"by_item": str(item.id), "by_title": item.title},
        ))
    return unblocked


async def reopen_item(db: AsyncSession, item: Item, actor: str) -> Item:
    """Reabre un ítem terminal: vuelve a backlog."""
    if item.status not in TERMINAL:
        raise TransitionError("Solo se reabren ítems en estado 'hecho' o 'descartado'.")
    old = item.status
    item.status = "backlog"
    item.closed_at = None
    await db.flush()
    db.add(ItemEvent(
        item_id=item.id, actor=actor, action="reopened",
        payload={"from": old, "to": "backlog"},
    ))
    return item


async def set_priority(db: AsyncSession, item: Item, priority: str | None, actor: str) -> Item:
    """Ajusta la prioridad humana. Lo declarado por el humano queda registrado
    en priority_declared (gana al juicio IA en orden/matriz)."""
    item.priority = priority
    item.priority_declared = priority
    await db.flush()
    db.add(ItemEvent(
        item_id=item.id, actor=actor, action="priority_changed",
        payload={"priority": priority},
    ))
    return item


async def touch_embedding_available(db: AsyncSession) -> bool:
    """True si la columna embedding existe y tiene al menos un valor no-NULL (capa semántica)."""
    try:
        result = await db.execute(text("SELECT 1 FROM items WHERE embedding IS NOT NULL LIMIT 1"))
        return result.first() is not None
    except Exception:
        return False


# ---------- Listado + ordenamiento (DUP-1) ----------
#
# Única implementación del listado de ítems con filtros + orden, consumida por REST,
# UI y MCP. El orden topológico usa el grafo (graph.topological_order).

async def _resolve_scope_id(db: AsyncSession, scope: Any) -> uuid.UUID | None:
    """Acepta un scope como uuid.UUID o como nombre (str). Devuelve el id o None si no existe."""
    if scope is None:
        return None
    if isinstance(scope, uuid.UUID):
        return scope
    # str: puede ser un UUID en texto o el nombre del scope.
    try:
        return uuid.UUID(str(scope))
    except (ValueError, AttributeError):
        row = await db.scalar(select(Scope).where(Scope.name == str(scope)))
        return row.id if row else None


def _topo_rank(items: list[Item], edges: list[tuple[str, str, str]]) -> dict[str, int]:
    """Mapa id→posición según el orden topológico del grafo de precedencia."""
    ids = [str(i.id) for i in items]
    if not ids:
        return {}
    impact = {str(i.id): (i.impact_ai or 0) for i in items}
    result = graph.topological_order(ids, edges, impact)
    return {item_id: rank for rank, item_id in enumerate(result["order"])}


async def _topo_order_ids(db: AsyncSession, items: list[Item]) -> dict[str, int]:
    """Calcula el rango topológico cargando los arcos internos al conjunto de ítems."""
    ids = [str(i.id) for i in items]
    if not ids:
        return {}
    rels = await db.execute(
        text("""
            SELECT source_id, target_id, relation FROM item_relationships
            WHERE source_id = ANY(:ids) AND target_id = ANY(:ids)
        """),
        {"ids": ids},
    )
    edges = [(str(r["source_id"]), str(r["target_id"]), r["relation"]) for r in rels.mappings().all()]
    return _topo_rank(items, edges)


def _order_items(items: list[Item], order: str, topo_rank: dict[str, int] | None) -> list[Item]:
    """Ordena en memoria por impacto / prioridad / topológico / reciente (fallback)."""
    if order == "impacto":
        return sorted(items, key=lambda i: (-(i.impact_ai or 0), i.effort_ai or "ZZ"))
    if order == "prioridad":
        return sorted(items, key=lambda i: (_PRIORITY_RANK.get(i.priority, 9), -(i.impact_ai or 0)))
    if order == "topologico" and topo_rank is not None:
        return sorted(items, key=lambda i: topo_rank.get(str(i.id), 1_000_000))
    return sorted(items, key=lambda i: i.created_at, reverse=True)


def _apply_item_filters(
    q: "Select[Any]",
    *,
    scope_id: uuid.UUID | None,
    statuses: list[str] | None,
    type: str | None,
    stale_risk: bool | None,
    quickwins: bool,
) -> "Select[Any]":
    if scope_id is not None:
        q = q.where(Item.scope_id == scope_id)
    if statuses:
        q = q.where(Item.status.in_(statuses))
    if type:
        q = q.where(Item.type == type)
    if stale_risk is not None:
        q = q.where(Item.stale_risk == stale_risk)
    if quickwins:
        q = q.where(Item.impact_ai >= 4, Item.effort_ai.in_(["XS", "S"]))
    return q


async def list_items(
    db: AsyncSession,
    *,
    scope: Any = None,
    statuses: list[str] | None = None,
    type: str | None = None,
    order: str = "impacto",
    quickwins: bool = False,
    stale_risk: bool | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[Item]:
    """Lista ítems con filtros + orden. Implementación única (REST/UI/MCP).

    Args:
        scope: uuid.UUID o nombre del scope (str). None = todos los scopes.
        statuses: lista de estados a incluir. None = todos.
        type: tipo de ítem (uno solo). None = todos.
        order: "impacto" | "prioridad" | "topologico" | "reciente".
        quickwins: si True, solo ítems de alto impacto (>=4) y bajo esfuerzo (XS/S).
        stale_risk: filtra por la bandera de riesgo de obsolescencia.
        limit / offset: paginación. limit=None trae todo el conjunto filtrado.

    El orden se aplica en memoria sobre el conjunto traído (incluido el topológico,
    que necesita el grafo). Devuelve la lista de Items ordenada.
    """
    scope_id = await _resolve_scope_id(db, scope)
    # Si se pidió un scope inexistente por nombre, el resultado es vacío (no "todos").
    if scope is not None and scope_id is None:
        return []

    q = _apply_item_filters(
        select(Item),
        scope_id=scope_id, statuses=statuses, type=type,
        stale_risk=stale_risk, quickwins=quickwins,
    )
    if offset:
        q = q.offset(offset)
    if limit is not None:
        q = q.limit(limit)

    items = list((await db.execute(q)).scalars().all())
    topo_rank = await _topo_order_ids(db, items) if order == "topologico" else None
    return _order_items(items, order, topo_rank)
