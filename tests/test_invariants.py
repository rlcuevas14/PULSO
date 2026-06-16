"""Invariantes del hardening: expiración de tokens (SEC-03) y consistencia enum↔CHECK (DM-01).

Tests a nivel BD usando el fixture `db`. No tocan app/ — solo verifican que el código de
servicio y los CHECK constraints reales se comportan según el contrato.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import ApiToken
from app.auth.service import _hash_token, create_user, verify_api_token
from app.enums import (
    ITEM_STATUSES,
    ITEM_TYPES,
    ORIGENES,
    PRIORITIES,
)


# --------------------------------------------------------------------------- #
# SEC-03: expiración de tokens.
# --------------------------------------------------------------------------- #
async def _token_with_expiry(db: AsyncSession, expires_at: datetime | None) -> str:
    """Crea un ApiToken con expires_at explícito (la API normal no lo expone)."""
    suffix = uuid.uuid4().hex[:8]
    user = await create_user(db, f"exp{suffix}@test.cl", "Exp", "pass", "admin")
    raw = f"raw-{uuid.uuid4().hex}"
    token = ApiToken(
        name=f"exp-{suffix}", token_hash=_hash_token(raw), scopes="write",
        created_by=user.id, expires_at=expires_at,
    )
    db.add(token)
    await db.commit()
    return raw


@pytest.mark.asyncio
async def test_token_expirado_rechazado(db: AsyncSession):
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    raw = await _token_with_expiry(db, past)
    assert await verify_api_token(db, raw) is None


@pytest.mark.asyncio
async def test_token_con_expiry_futuro_aceptado(db: AsyncSession):
    future = datetime.now(timezone.utc) + timedelta(days=1)
    raw = await _token_with_expiry(db, future)
    token = await verify_api_token(db, raw)
    assert token is not None
    assert token.scopes == "write"


@pytest.mark.asyncio
async def test_token_sin_expiry_no_caduca(db: AsyncSession):
    raw = await _token_with_expiry(db, None)
    assert await verify_api_token(db, raw) is not None


# --------------------------------------------------------------------------- #
# DM-01: consistencia enum (app.enums) ↔ CHECK real en la BD.
# --------------------------------------------------------------------------- #
def test_tuplas_de_enums_no_vacias():
    """Guardia barata: ninguna tupla de dominio puede quedar vacía (rompería los CHECK)."""
    for name, values in (
        ("ITEM_TYPES", ITEM_TYPES), ("ITEM_STATUSES", ITEM_STATUSES),
        ("PRIORITIES", PRIORITIES), ("ORIGENES", ORIGENES),
    ):
        assert len(values) > 0, f"{name} no puede estar vacía"
        assert all(isinstance(v, str) and v for v in values), f"{name} tiene valores vacíos"


@pytest.mark.asyncio
async def test_check_acepta_cada_valor_valido_de_type(db: AsyncSession):
    """Cada valor de ITEM_TYPES debe pasar el CHECK items_type_check (INSERT real)."""
    scope_id = await _make_scope(db)
    for t in ITEM_TYPES:
        await db.execute(text(
            "INSERT INTO items (id, scope_id, title, type, status, origen, "
            "stale_risk, agent_ready) "
            "VALUES (:id, :sid, :title, :type, 'backlog', 'human', false, false)"
        ), {"id": str(uuid.uuid4()), "sid": scope_id, "title": f"t-{t}", "type": t})
    await db.flush()  # si algún valor fuera inválido, el flush lanzaría IntegrityError
    n = await db.scalar(
        text("SELECT count(*) FROM items WHERE scope_id = :sid"), {"sid": scope_id}
    )
    assert n == len(ITEM_TYPES)
    await db.rollback()


@pytest.mark.asyncio
async def test_check_rechaza_type_fuera_de_la_tupla(db: AsyncSession):
    """Un type que NO está en ITEM_TYPES debe violar el CHECK (IntegrityError)."""
    scope_id = await _make_scope(db)
    with pytest.raises(IntegrityError):
        await db.execute(text(
            "INSERT INTO items (id, scope_id, title, type, status, origen, "
            "stale_risk, agent_ready) "
            "VALUES (:id, :sid, 't', 'deuda', 'backlog', 'human', false, false)"
        ), {"id": str(uuid.uuid4()), "sid": scope_id})
        await db.flush()
    await db.rollback()


@pytest.mark.asyncio
async def test_check_rechaza_status_fuera_de_la_tupla(db: AsyncSession):
    scope_id = await _make_scope(db)
    with pytest.raises(IntegrityError):
        await db.execute(text(
            "INSERT INTO items (id, scope_id, title, type, status, origen, "
            "stale_risk, agent_ready) "
            "VALUES (:id, :sid, 't', 'feature', 'volando', 'human', false, false)"
        ), {"id": str(uuid.uuid4()), "sid": scope_id})
        await db.flush()
    await db.rollback()


@pytest.mark.asyncio
async def test_check_rechaza_origen_fuera_de_la_tupla(db: AsyncSession):
    scope_id = await _make_scope(db)
    with pytest.raises(IntegrityError):
        await db.execute(text(
            "INSERT INTO items (id, scope_id, title, type, status, origen, "
            "stale_risk, agent_ready) "
            "VALUES (:id, :sid, 't', 'feature', 'backlog', 'marciano', false, false)"
        ), {"id": str(uuid.uuid4()), "sid": scope_id})
        await db.flush()
    await db.rollback()


async def _make_scope(db: AsyncSession) -> str:
    from app.scopes.models import Scope

    scope = Scope(name=f"inv-{uuid.uuid4().hex[:8]}")
    db.add(scope)
    await db.flush()
    return str(scope.id)
