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


_SONNET_MODEL = "claude-sonnet-4-6"

_STAGE_PROMPT = """Eres un ingeniero de producto. Genera el artefacto del stage "{stage}" para
este hilo de desarrollo, en español (tuteo neutro, sin voseo). Devuelve SOLO markdown.

Hilo: {title}
Resumen: {summary}

Artefactos previos:
{artifacts}

Genera el contenido del stage "{stage}":"""


async def generate_stage(stage: str, title: str, summary: str | None, artifacts: str) -> dict[str, Any]:
    """Genera el borrador de un stage de hilo (Sonnet para spec, Haiku para el resto)."""
    if not settings.anthropic_api_key:
        raise LLMUnavailable("ANTHROPIC_API_KEY no configurada")
    model = _SONNET_MODEL if stage == "spec" else _HAIKU_MODEL
    prompt = _STAGE_PROMPT.format(
        stage=stage, title=title, summary=summary or "(sin resumen)",
        artifacts=artifacts or "(ninguno)",
    )
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            _ANTHROPIC_URL,
            headers={
                "x-api-key": settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={"model": model, "max_tokens": 2000,
                  "messages": [{"role": "user", "content": prompt}]},
        )
        resp.raise_for_status()
        data = resp.json()
    content = "".join(block.get("text", "") for block in data.get("content", []))
    return {"content": content, "model": model}


_TRIAGE_PROMPT = """Eres un ingeniero de confiabilidad. Clasifica este error de Sentry y
responde SOLO con JSON.

Título: {title}
Contexto: {context}

Devuelve {{"triage": "<una de: bug-real, input-malo, 3rd-party, ruido>"}}.
- bug-real: bug genuino de nuestro código que hay que arreglar.
- input-malo: error por datos/entrada inválida del usuario, no es bug.
- 3rd-party: falla de un servicio externo, no de nuestro código.
- ruido: transitorio/irrelevante (timeout aislado, bot, healthcheck).

JSON:"""


async def triage_sentry(title: str, context: str) -> dict[str, Any]:
    """Clasifica un error de Sentry con Haiku. Lanza LLMUnavailable sin API key."""
    if not settings.anthropic_api_key:
        raise LLMUnavailable("ANTHROPIC_API_KEY no configurada")
    prompt = _TRIAGE_PROMPT.format(title=title, context=context[:2000])
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            _ANTHROPIC_URL,
            headers={"x-api-key": settings.anthropic_api_key,
                     "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": _HAIKU_MODEL, "max_tokens": 100,
                  "messages": [{"role": "user", "content": prompt}]},
        )
        resp.raise_for_status()
        data = resp.json()
    text = "".join(b.get("text", "") for b in data.get("content", []))
    triage = str(_extract_json(text).get("triage", "")).strip()
    if triage not in ("bug-real", "input-malo", "3rd-party", "ruido"):
        triage = "bug-real"  # default seguro: si dudas, trátalo como bug real
    return {"triage": triage}


async def embed_text(content: str) -> list[float] | None:
    """Embedding Gemini (768 dim). Devuelve None si no hay API key (degradación).

    SEC-07: la API key viaja en el header `x-goog-api-key`, no en la query string
    `?key=`, para que no quede registrada en logs de acceso/proxies (las query strings
    se loguean por defecto; los headers no).

    PERF-03: timeout agresivo (8s). El embedding es opcional — si el proveedor tarda,
    degradamos a None en vez de bloquear el request/worker.
    """
    if not settings.gemini_api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(
                _GEMINI_EMBED_URL,
                headers={"x-goog-api-key": settings.gemini_api_key},
                json={
                    "model": "models/gemini-embedding-001",
                    "content": {"parts": [{"text": content[:8000]}]},
                    "outputDimensionality": EMBED_DIM,
                },
            )
            resp.raise_for_status()
    except httpx.TimeoutException:
        return None
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
