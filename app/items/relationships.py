"""Gestión de arcos del grafo: creación con resolución por texto + guards, y borrado.

Compartido por la API REST, la UI y el MCP. Resuelve queries de texto a ítems por
full-text; aborta ante ambigüedad (empate de rank) en vez de adivinar.
"""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.items.models import ItemRelationship

RELATIONS = ("blocks", "requires", "conflicts", "related", "part_of")
_SYMMETRIC = ("conflicts", "related")


class RelationshipError(ValueError):
    """Error de negocio al crear/borrar un arco (no encontrado, ambiguo, self-loop, duplicado)."""


async def resolve_query(db: AsyncSession, query: str) -> uuid.UUID:
    """Resuelve un texto a un item_id por full-text. Aborta si hay empate de rank en el top-2."""
    result = await db.execute(
        text("""
            SELECT id, ts_rank(search_vector, plainto_tsquery('spanish', :q)) AS rank
            FROM items
            WHERE search_vector @@ plainto_tsquery('spanish', :q)
            ORDER BY rank DESC, id
            LIMIT 2
        """),
        {"q": query},
    )
    rows = result.mappings().all()
    if not rows:
        raise RelationshipError(f"No se encontró ningún ítem para «{query}».")
    if len(rows) == 2 and float(rows[0]["rank"]) == float(rows[1]["rank"]):
        raise RelationshipError(
            f"«{query}» es ambiguo (varios ítems con el mismo rank). Especifica el ítem exacto."
        )
    return rows[0]["id"]


async def create_relationship(
    db: AsyncSession,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    relation: str,
    note: str | None = None,
) -> ItemRelationship:
    if relation not in RELATIONS:
        raise RelationshipError(f"Relación inválida: {relation}")
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
