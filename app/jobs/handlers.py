"""Handlers de jobs del worker. Sprint 1: enrich real (Haiku + embeddings)."""

import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import llm
from app.items.models import AiEnrichment, Item


async def handle_enrich(db: AsyncSession, ref_id: uuid.UUID | None) -> dict:
    """Enriquece un ítem con impacto/esfuerzo (Haiku) y embedding (Gemini).

    Degrada con gracia: sin ANTHROPIC_API_KEY no estima; sin GEMINI_API_KEY o sin
    pgvector no genera embedding. Nunca rompe el worker.
    """
    if ref_id is None:
        return {"status": "sin-ref"}

    item = (await db.execute(select(Item).where(Item.id == ref_id))).scalar_one_or_none()
    if item is None:
        return {"status": "item-no-encontrado"}

    out: dict = {"item_id": str(ref_id)}

    # 1) Estimación impacto/esfuerzo con Haiku.
    try:
        result = await llm.enrich_item(item.title, item.summary_md, item.type)
        item.impact_ai = result["impact"]
        item.effort_ai = result["effort"]
        item.impact_rationale = result["rationale"]
        db.add(AiEnrichment(
            item_id=item.id,
            model=result["model"],
            prompt_version="v1",
            effort=result["effort"],
            impact=result["impact"],
            rationale=result["rationale"],
            tokens_in=result.get("tokens_in"),
            tokens_out=result.get("tokens_out"),
        ))
        out["enriched"] = True
    except llm.LLMUnavailable:
        out["enriched"] = False
        out["note"] = "sin ANTHROPIC_API_KEY"

    # 2) Embedding (best-effort: requiere GEMINI_API_KEY + pgvector).
    try:
        vec = await llm.embed_text(f"{item.title}\n{item.summary_md or ''}")
        if vec:
            await db.execute(
                text("UPDATE items SET embedding = CAST(:vec AS vector) WHERE id = :id"),
                {"vec": str(vec), "id": str(item.id)},
            )
            out["embedded"] = True
    except Exception as exc:  # pgvector ausente o error de red — no romper el job
        out["embedded"] = False
        out["embed_error"] = str(exc)[:120]

    await db.flush()
    return out


HANDLERS = {
    "enrich": handle_enrich,
}
