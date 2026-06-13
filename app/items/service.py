"""Lógica de mutación de ítems, compartida por la API JSON, la UI y el MCP.

Centraliza la validación de transiciones (lifecycle) y la auditoría (ItemEvent),
de modo que UI / REST / MCP nunca diverjan.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.items import graph
from app.items.lifecycle import TERMINAL, valid_transition
from app.items.models import Item, ItemEvent


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
