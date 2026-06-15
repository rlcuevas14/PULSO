"""Búsqueda full-text única sobre items (ts_rank / plainto_tsquery 'spanish').

Una sola implementación del FTS, consumida por REST (/items/search), el MCP
(pulso_buscar) y la resolución por texto del grafo (relationships.resolve_query).
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def search_items(
    db: AsyncSession,
    q: str,
    *,
    limit: int = 50,
    with_scope: bool = False,
) -> list[dict[str, Any]]:
    """Busca ítems por full-text en español, ordenados por rank descendente.

    Devuelve dicts con: id, title, summary_md, type, status, scope_id, effort_ai,
    impact_ai, stale_risk y rank. Si `with_scope=True`, incluye además `scope`
    (nombre del scope, vía JOIN). Los ids vienen como str.

    Dedup: la query agrupa por ítem (un ítem aparece una vez) y ordena por rank, id.
    """
    if with_scope:
        sql = """
            SELECT i.id, i.title, i.summary_md, i.type, i.status, i.scope_id,
                   s.name AS scope, i.effort_ai, i.impact_ai, i.stale_risk,
                   ts_rank(i.search_vector, plainto_tsquery('spanish', :q)) AS rank
            FROM items i JOIN scopes s ON s.id = i.scope_id
            WHERE i.search_vector @@ plainto_tsquery('spanish', :q)
            ORDER BY rank DESC, i.id
            LIMIT :limit
        """
    else:
        sql = """
            SELECT id, title, summary_md, type, status, scope_id,
                   effort_ai, impact_ai, stale_risk,
                   ts_rank(search_vector, plainto_tsquery('spanish', :q)) AS rank
            FROM items
            WHERE search_vector @@ plainto_tsquery('spanish', :q)
            ORDER BY rank DESC, id
            LIMIT :limit
        """
    rows = (await db.execute(text(sql), {"q": q, "limit": int(limit)})).mappings().all()
    out: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        row["id"] = str(row["id"])
        row["scope_id"] = str(row["scope_id"]) if row.get("scope_id") else None
        row["rank"] = float(row["rank"]) if row.get("rank") is not None else 0.0
        out.append(row)
    return out
