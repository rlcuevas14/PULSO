"""Importador idempotente de items desde archivos JSONL del digest."""

import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.items.models import Item
from app.scopes.models import Scope

_STATUS_MAP = {
    "abierto": "backlog",
    "diferido": "backlog",
    "en-curso": "en-curso",
    "bloqueado": "bloqueado",
}

_VALID_TYPES = {
    "bug", "feature", "tech-debt", "infra", "docs",
    "ops", "seguridad", "producto", "idea"
}
_VALID_EFFORTS = {"XS", "S", "M", "L", "XL"}


def _hash_line(raw_line: str) -> str:
    return hashlib.sha256(raw_line.strip().encode()).hexdigest()


def _normalize(obj: dict[str, Any]) -> dict[str, Any] | None:
    """Convierte un item del JSONL al schema de BD. Retorna None si inválido."""
    title = (obj.get("title") or "").strip()
    scope_name = (obj.get("scope") or "").strip().lower()
    item_type = (obj.get("type") or "").strip()

    if not title or not scope_name or item_type not in _VALID_TYPES:
        return None

    raw_status = (obj.get("status") or "abierto").strip()
    db_status = _STATUS_MAP.get(raw_status, "backlog")

    priority_declared = (obj.get("priority_declared") or "").strip()
    if raw_status == "diferido" and priority_declared:
        priority_declared = f"DIFERIDO — {priority_declared}"
    elif raw_status == "diferido":
        priority_declared = "DIFERIDO"

    raw_effort = (obj.get("effort_ai") or "").strip()
    effort_ai = raw_effort if raw_effort in _VALID_EFFORTS else None

    raw_impact = obj.get("impact_ai")
    try:
        impact_ai = int(raw_impact) if raw_impact is not None else None
        if impact_ai is not None and not (1 <= impact_ai <= 5):
            impact_ai = None
    except (ValueError, TypeError):
        impact_ai = None

    return {
        "scope_name": scope_name,
        "title": title,
        "summary_md": (obj.get("summary") or "").strip() or None,
        "type": item_type,
        "status": db_status,
        "effort_ai": effort_ai,
        "impact_ai": impact_ai,
        "impact_rationale": (obj.get("impact_rationale") or "").strip() or None,
        "effort_declared": (obj.get("effort_declared") or "").strip() or None,
        "priority_declared": priority_declared or None,
        "trigger_text": (obj.get("trigger") or "").strip() or None,
        "dependencies": (obj.get("dependencies") or "").strip() or None,
        "stale_risk": bool(obj.get("stale_risk", False)),
        "origen": "digest",
    }


async def _get_or_create_scope(db: AsyncSession, name: str) -> Scope:
    result = await db.execute(select(Scope).where(Scope.name == name))
    scope = result.scalar_one_or_none()
    if scope is None:
        scope = Scope(name=name, source_repo="efrain")
        db.add(scope)
        await db.flush()
    return scope


async def import_jsonl(db: AsyncSession, path: Path) -> dict[str, int]:
    """Importa un archivo JSONL. Retorna {imported, skipped_duplicate, skipped_invalid}."""
    existing_hashes: set[str] = set()
    result = await db.execute(select(Item.source_refs).where(Item.origen == "digest"))
    for (source_refs,) in result:
        if isinstance(source_refs, dict):
            h = source_refs.get("_import_hash")
            if h:
                existing_hashes.add(h)

    scope_cache: dict[str, Scope] = {}
    imported = skipped_dup = skipped_invalid = 0

    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue

        line_hash = _hash_line(raw_line)
        if line_hash in existing_hashes:
            skipped_dup += 1
            continue

        try:
            obj = json.loads(raw_line)
        except json.JSONDecodeError:
            skipped_invalid += 1
            continue

        normalized = _normalize(obj)
        if normalized is None:
            skipped_invalid += 1
            continue

        scope_name = normalized.pop("scope_name")
        if scope_name not in scope_cache:
            scope_cache[scope_name] = await _get_or_create_scope(db, scope_name)
        scope = scope_cache[scope_name]

        item = Item(
            **normalized,
            scope_id=scope.id,
            source_refs={
                "_import_hash": line_hash,
                "source": obj.get("source", ""),
                "date_hint": obj.get("date_hint", ""),
            },
        )
        db.add(item)
        existing_hashes.add(line_hash)
        imported += 1

    await db.commit()
    return {"imported": imported, "skipped_duplicate": skipped_dup, "skipped_invalid": skipped_invalid}


async def import_directory(db: AsyncSession, directory: Path) -> dict[str, int]:
    """Importa todos los *.jsonl de un directorio."""
    totals = {"imported": 0, "skipped_duplicate": 0, "skipped_invalid": 0}
    for f in sorted(directory.glob("*.jsonl")):
        result = await import_jsonl(db, f)
        for k in totals:
            totals[k] += result[k]
    return totals
