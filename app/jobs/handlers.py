"""Handlers de jobs. F1: stubs. F2: implementar enrich real."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession


async def handle_enrich(db: AsyncSession, ref_id: uuid.UUID | None) -> dict:
    """Stub F1 — F2 implementará el análisis IA real."""
    return {"status": "stub", "message": "Enriquecimiento IA pendiente (F2)"}


HANDLERS = {
    "enrich": handle_enrich,
}
