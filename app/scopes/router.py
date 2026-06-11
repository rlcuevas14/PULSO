import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import api_or_session_user
from app.database import get_db
from app.scopes.models import Scope

router = APIRouter(prefix="/scopes", tags=["scopes"])


class ScopeCreate(BaseModel):
    name: str
    description: str | None = None
    color: str | None = None
    source_repo: str | None = None
    display_order: int = 0


class ScopePatch(BaseModel):
    description: str | None = None
    color: str | None = None
    archived: bool | None = None
    display_order: int | None = None


class ScopeOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    color: str | None
    source_repo: str | None
    archived: bool
    display_order: int

    model_config = {"from_attributes": True}


@router.get("", response_model=list[ScopeOut])
async def list_scopes(
    db: AsyncSession = Depends(get_db),
    _auth=Depends(api_or_session_user),
):
    result = await db.execute(select(Scope).order_by(Scope.display_order, Scope.name))
    return result.scalars().all()


@router.post("", response_model=ScopeOut, status_code=201)
async def create_scope(
    body: ScopeCreate,
    db: AsyncSession = Depends(get_db),
    _auth=Depends(api_or_session_user),
):
    scope = Scope(**body.model_dump())
    db.add(scope)
    try:
        await db.commit()
        await db.refresh(scope)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"El scope '{body.name}' ya existe")
    return scope


@router.patch("/{scope_id}", response_model=ScopeOut)
async def patch_scope(
    scope_id: uuid.UUID,
    body: ScopePatch,
    db: AsyncSession = Depends(get_db),
    _auth=Depends(api_or_session_user),
):
    result = await db.execute(select(Scope).where(Scope.id == scope_id))
    scope = result.scalar_one_or_none()
    if scope is None:
        raise HTTPException(status_code=404, detail="Scope no encontrado")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(scope, field, value)
    await db.commit()
    await db.refresh(scope)
    return scope
