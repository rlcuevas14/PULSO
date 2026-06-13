"""Interfaz aislada y mockeable a los LLMs (Anthropic Haiku + embeddings Gemini).

Aislada a propósito: los handlers la llaman a través de estas funciones para que los
tests las parcheen sin tocar la red ni gastar tokens. Degradan con gracia sin API key.
"""

import json
from typing import Any

import httpx

from app.config import settings

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_GEMINI_EMBED_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent"
)
EMBED_DIM = 768


class LLMUnavailable(RuntimeError):
    """No hay API key configurada para el proveedor."""


_ENRICH_PROMPT = """Eres un analista de producto. Evalúa este ítem de backlog y responde SOLO con JSON.

Título: {title}
Tipo: {item_type}
Resumen: {summary}

Devuelve un objeto JSON con exactamente estas claves:
- "impact": entero 1-5 (5 = altísimo impacto en usuarios/negocio)
- "effort": una de "XS","S","M","L","XL" (esfuerzo estimado de implementación)
- "rationale": una frase breve en español (tuteo neutro) justificando el impacto

JSON:"""


async def enrich_item(title: str, summary: str | None, item_type: str) -> dict[str, Any]:
    """Llama a Haiku para estimar impacto/esfuerzo. Lanza LLMUnavailable sin API key."""
    if not settings.anthropic_api_key:
        raise LLMUnavailable("ANTHROPIC_API_KEY no configurada")

    prompt = _ENRICH_PROMPT.format(title=title, item_type=item_type, summary=summary or "(sin resumen)")
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            _ANTHROPIC_URL,
            headers={
                "x-api-key": settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": _HAIKU_MODEL,
                "max_tokens": 300,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        data = resp.json()

    text = "".join(block.get("text", "") for block in data.get("content", []))
    parsed = _extract_json(text)
    usage = data.get("usage", {})
    return {
        "impact": _clamp_impact(parsed.get("impact")),
        "effort": _clamp_effort(parsed.get("effort")),
        "rationale": str(parsed.get("rationale", "")).strip() or None,
        "tokens_in": usage.get("input_tokens"),
        "tokens_out": usage.get("output_tokens"),
        "model": _HAIKU_MODEL,
    }


async def embed_text(content: str) -> list[float] | None:
    """Embedding Gemini (768 dim). Devuelve None si no hay API key (degradación)."""
    if not settings.gemini_api_key:
        return None
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{_GEMINI_EMBED_URL}?key={settings.gemini_api_key}",
            json={
                "model": "models/gemini-embedding-001",
                "content": {"parts": [{"text": content[:8000]}]},
                "outputDimensionality": EMBED_DIM,
            },
        )
        resp.raise_for_status()
        values = resp.json().get("embedding", {}).get("values")
        return values if values else None


def _extract_json(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        result: dict[str, Any] = json.loads(text[start : end + 1])
        return result
    except json.JSONDecodeError:
        return {}


def _clamp_impact(v: Any) -> int | None:
    try:
        n = int(v)
        return max(1, min(5, n))
    except (TypeError, ValueError):
        return None


def _clamp_effort(v: Any) -> str | None:
    s = str(v).upper().strip() if v is not None else ""
    return s if s in ("XS", "S", "M", "L", "XL") else None
