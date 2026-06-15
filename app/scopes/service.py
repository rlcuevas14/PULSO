"""Lógica de servicio de scopes (agrupadores), compartida por REST, UI, MCP y webhooks.

Única fuente de verdad para resolver/crear/actualizar scopes. `resolve_scope` hace
match case-insensitive para no crear duplicados ("Currículo" == "curriculo") — corrige
el bug de duplicados case-sensitive que tenían los `_get_or_create_scope` locales del
importador y de los webhooks.

Disciplina de transacciones: los servicios SOLO hacen `flush`; el commit lo hace el
borde (router/dispatch).
"""

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.scopes.models import Scope


class ScopeError(ValueError):
    """Error de negocio sobre scopes (nombre vacío, no encontrado)."""


async def resolve_scope(
    db: AsyncSession,
    name: str,
    *,
    create: bool,
    source_repo: str | None = None,
) -> Scope:
    """Resuelve un scope por nombre (case-insensitive). Si no existe y create=True, lo crea.

    Match: ``lower(scopes.name) == name.strip().lower()`` — evita duplicados por mayúsculas.
    Si create=False y no existe → ScopeError. Si el nombre es vacío → ScopeError.

    El commit lo hace el borde; aquí solo se hace flush al crear (para tener el id).
    """
    cleaned = (name or "").strip()
    if not cleaned:
        raise ScopeError("El nombre del scope no puede estar vacío.")

    scope = (await db.execute(
        select(Scope).where(func.lower(Scope.name) == cleaned.lower())
    )).scalar_one_or_none()
    if scope is not None:
        return scope

    if not create:
        raise ScopeError(
            f"Scope «{cleaned}» no existe. Usa la lista de scopes para ver los disponibles."
        )

    scope = Scope(name=cleaned[:60], source_repo=source_repo)
    db.add(scope)
    await db.flush()
    return scope


async def create_scope(db: AsyncSession, data: dict[str, Any]) -> Scope:
    """Crea un scope con los campos provistos. Valida nombre no vacío.

    No traduce IntegrityError (uniqueness de name) — eso lo hace el borde (router → 409).
    Solo flush; el commit lo hace el borde.
    """
    name = (data.get("name") or "").strip()
    if not name:
        raise ScopeError("El nombre del scope no puede estar vacío.")
    payload = {**data, "name": name}
    scope = Scope(**payload)
    db.add(scope)
    await db.flush()
    return scope


async def update_scope(db: AsyncSession, scope_id: uuid.UUID, changes: dict[str, Any]) -> Scope:
    """Aplica cambios parciales a un scope existente. ScopeError si no existe.

    Solo asigna los campos presentes en `changes` (el borde decide qué excluir, p.ej. None).
    Solo flush; el commit lo hace el borde.
    """
    scope = (await db.execute(select(Scope).where(Scope.id == scope_id))).scalar_one_or_none()
    if scope is None:
        raise ScopeError("Scope no encontrado")
    for field, value in changes.items():
        setattr(scope, field, value)
    await db.flush()
    return scope
