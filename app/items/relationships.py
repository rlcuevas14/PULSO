"""Gestión de arcos del grafo: creación con resolución por texto + guards, y borrado.

Compartido por la API REST, la UI y el MCP. Resuelve queries de texto a ítems por
full-text (vía app.items.search); aborta ante ambigüedad (empate de rank) en vez de
adivinar, y marca baja confianza cuando el margen entre el top-1 y el top-2 es estrecho.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import RELATIONS
from app.items.models import ItemRelationship
from app.items.search import search_items

_SYMMETRIC = ("conflicts", "related")
# Margen relativo mínimo entre top-1 y top-2 para considerar la resolución "confiable".
# Si (rank0 - rank1) / rank0 < este umbral, la coincidencia es de baja confianza.
_LOW_CONFIDENCE_MARGIN = 0.15


class RelationshipError(ValueError):
    """Error de negocio al crear/borrar un arco (no encontrado, ambiguo, self-loop, duplicado)."""


async def resolve_query_verbose(db: AsyncSession, query: str) -> dict:
    """Resuelve un texto a un ítem por full-text, con metadatos de confianza.

    Devuelve {"id": uuid, "title": str, "low_confidence": bool, "margin": float}.
    Aborta (RelationshipError) si no hay resultados o si el top-2 empata exactamente.
    `low_confidence=True` cuando el margen relativo entre el top-1 y el top-2 es estrecho
    (la coincidencia es plausible pero no clara) — el caller puede advertirlo al usuario.
    """
    rows = await search_items(db, query, limit=2)
    if not rows:
        raise RelationshipError(f"No se encontró ningún ítem para «{query}».")

    rank0 = rows[0]["rank"]
    low_confidence = False
    margin = 1.0
    if len(rows) == 2:
        rank1 = rows[1]["rank"]
        if rank0 == rank1:
            raise RelationshipError(
                f"«{query}» es ambiguo (varios ítems con el mismo rank). Especifica el ítem exacto."
            )
        margin = (rank0 - rank1) / rank0 if rank0 else 0.0
        low_confidence = margin < _LOW_CONFIDENCE_MARGIN

    return {
        "id": uuid.UUID(rows[0]["id"]),
        "title": rows[0]["title"],
        "low_confidence": low_confidence,
        "margin": margin,
    }


async def resolve_query(db: AsyncSession, query: str) -> uuid.UUID:
    """Resuelve un texto a un item_id por full-text. Aborta si hay empate de rank en el top-2.

    Compatibilidad: devuelve solo el id (como antes). Para advertir baja confianza,
    usa `resolve_query_verbose`.
    """
    return (await resolve_query_verbose(db, query))["id"]


async def create_relationship(
    db: AsyncSession,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    relation: str,
    note: str | None = None,
) -> ItemRelationship:
    if relation not in RELATIONS:
        raise RelationshipError(
            f"Invalid relation '{relation}'. Use one of: {', '.join(RELATIONS)}."
        )
    if source_id == target_id:
        raise RelationshipError("Un ítem no puede relacionarse consigo mismo.")

    # Para arcos simétricos, normalizar el orden para no duplicar (A,B) y (B,A).
    s, t = source_id, target_id
    if relation in _SYMMETRIC and str(source_id) > str(target_id):
        s, t = target_id, source_id

    # Idempotencia: si ya existe, devolver el existente (no 500 por PK duplicada).
    existing = await db.get(ItemRelationship, {"source_id": s, "target_id": t, "relation": relation})
    if existing is not None:
        return existing

    rel = ItemRelationship(source_id=s, target_id=t, relation=relation, note=note)
    db.add(rel)
    await db.flush()
    return rel


async def delete_relationship(
    db: AsyncSession, source_id: uuid.UUID, target_id: uuid.UUID, relation: str
) -> bool:
    rel = await db.get(
        ItemRelationship, {"source_id": source_id, "target_id": target_id, "relation": relation}
    )
    if rel is None:
        # Para simétricos, probar el orden invertido.
        if relation in _SYMMETRIC:
            rel = await db.get(
                ItemRelationship,
                {"source_id": target_id, "target_id": source_id, "relation": relation},
            )
    if rel is None:
        return False
    await db.delete(rel)
    await db.flush()
    return True
