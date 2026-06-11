import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.deps import api_or_session_user
from app.auth.models import ApiToken, User
from app.database import get_db
from app.items.models import AiEnrichment, Item, ItemComment, ItemEvent

router = APIRouter(prefix="/items", tags=["items"])


class ItemCreate(BaseModel):
    scope_id: uuid.UUID
    title: str
    type: str
    summary_md: str | None = None
    status: str = "backlog"
    priority: str | None = None
    effort_declared: str | None = None
    priority_declared: str | None = None
    trigger_text: str | None = None
    dependencies: str | None = None
    stale_risk: bool = False


class ItemPatch(BaseModel):
    title: str | None = None
    summary_md: str | None = None
    status: str | None = None
    priority: str | None = None
    stale_risk: bool | None = None
    agent_ready: bool | None = None


class CommentCreate(BaseModel):
    body_md: str
    kind: str = "comentario"


class CloseItem(BaseModel):
    status: str  # "hecho" | "descartado"
    reason: str | None = None


class ItemOut(BaseModel):
    id: uuid.UUID
    scope_id: uuid.UUID
    title: str
    summary_md: str | None
    type: str
    status: str
    priority: str | None
    effort_ai: str | None
    impact_ai: int | None
    stale_risk: bool
    agent_ready: bool
    origen: str
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None

    model_config = {"from_attributes": True}


class ItemDetail(ItemOut):
    impact_rationale: str | None
    effort_declared: str | None
    priority_declared: str | None
    trigger_text: str | None
    dependencies: str | None
    source_refs: Any = None
    events: list[dict]
    comments: list[dict]
    enrichments: list[dict]


def _actor(auth) -> str:
    if isinstance(auth, User):
        return auth.email
    if isinstance(auth, ApiToken):
        return f"token:{auth.name}"
    return "unknown"


@router.get("", response_model=list[ItemOut])
async def list_items(
    scope_id: uuid.UUID | None = Query(None),
    status: str | None = Query(None),
    type: str | None = Query(None),
    stale_risk: bool | None = Query(None),
    order: str = Query("reciente"),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(api_or_session_user),
):
    q = select(Item)
    if scope_id:
        q = q.where(Item.scope_id == scope_id)
    if status:
        q = q.where(Item.status == status)
    if type:
        q = q.where(Item.type == type)
    if stale_risk is not None:
        q = q.where(Item.stale_risk == stale_risk)
    if order == "impacto":
        q = q.order_by(Item.impact_ai.desc().nulls_last())
    else:
        q = q.order_by(Item.created_at.desc())
    result = await db.execute(q)
    return result.scalars().all()


@router.post("", response_model=ItemOut, status_code=201)
async def create_item(
    body: ItemCreate,
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_or_session_user),
):
    item = Item(**body.model_dump(), created_by=_actor(auth), origen="humano")
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


class ImportRequest(BaseModel):
    path: str | None = None
    directory: str | None = None


@router.post("/import/digest")
async def import_digest(
    body: ImportRequest,
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_or_session_user),
):
    if isinstance(auth, User) and auth.role != "admin":
        raise HTTPException(status_code=403, detail="Solo administradores pueden importar")

    target = body.path or body.directory
    if not target:
        raise HTTPException(status_code=422, detail="Proporcionar 'path' o 'directory'")

    p = Path(target)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Ruta no encontrada: {target}")

    from app.items.importer import import_directory, import_jsonl
    if p.is_dir():
        result = await import_directory(db, p)
    else:
        result = await import_jsonl(db, p)

    return result


@router.get("/{item_id}", response_model=ItemDetail)
async def get_item(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _auth=Depends(api_or_session_user),
):
    result = await db.execute(
        select(Item)
        .where(Item.id == item_id)
        .options(
            selectinload(Item.events),
            selectinload(Item.comments),
            selectinload(Item.enrichments),
        )
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Item no encontrado")

    def _event_dict(e):
        return {"id": str(e.id), "actor": e.actor, "action": e.action,
                "payload": e.payload, "created_at": e.created_at.isoformat()}

    def _comment_dict(c):
        return {"id": str(c.id), "author": c.author, "body_md": c.body_md,
                "kind": c.kind, "created_at": c.created_at.isoformat()}

    def _enrichment_dict(en):
        return {"id": str(en.id), "model": en.model, "effort": en.effort,
                "impact": en.impact, "rationale": en.rationale,
                "created_at": en.created_at.isoformat()}

    return ItemDetail(
        **{c.key: getattr(item, c.key) for c in Item.__table__.columns
           if c.key not in ("events", "comments", "enrichments")},
        events=[_event_dict(e) for e in item.events],
        comments=[_comment_dict(c) for c in item.comments],
        enrichments=[_enrichment_dict(e) for e in item.enrichments],
    )


@router.patch("/{item_id}", response_model=ItemOut)
async def patch_item(
    item_id: uuid.UUID,
    body: ItemPatch,
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_or_session_user),
):
    result = await db.execute(select(Item).where(Item.id == item_id))
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Item no encontrado")

    changes = body.model_dump(exclude_none=True)
    old_status = item.status
    for field, value in changes.items():
        setattr(item, field, value)

    await db.flush()

    if "status" in changes and changes["status"] != old_status:
        event = ItemEvent(
            item_id=item_id,
            actor=_actor(auth),
            action="status_changed",
            payload={"from": old_status, "to": changes["status"]},
        )
        db.add(event)

    await db.commit()
    await db.refresh(item)
    return item


@router.get("/{item_id}/comments/{comment_id}")
async def get_comment(
    item_id: uuid.UUID,
    comment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _auth=Depends(api_or_session_user),
):
    """Lectura de un comentario individual. Comentarios son append-only — no existe PATCH."""
    result = await db.execute(
        select(ItemComment).where(
            ItemComment.id == comment_id,
            ItemComment.item_id == item_id,
        )
    )
    comment = result.scalar_one_or_none()
    if comment is None:
        raise HTTPException(status_code=404, detail="Comentario no encontrado")
    return {
        "id": str(comment.id),
        "author": comment.author,
        "body_md": comment.body_md,
        "kind": comment.kind,
        "created_at": comment.created_at.isoformat(),
    }


@router.post("/{item_id}/comments", status_code=201)
async def add_comment(
    item_id: uuid.UUID,
    body: CommentCreate,
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_or_session_user),
):
    result = await db.execute(select(Item).where(Item.id == item_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Item no encontrado")

    comment = ItemComment(
        item_id=item_id,
        author=_actor(auth),
        body_md=body.body_md,
        kind=body.kind,
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)
    return {"id": str(comment.id), "created_at": comment.created_at.isoformat()}


@router.post("/{item_id}/close", response_model=ItemOut)
async def close_item(
    item_id: uuid.UUID,
    body: CloseItem,
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_or_session_user),
):
    if body.status not in ("hecho", "descartado"):
        raise HTTPException(status_code=422, detail="status debe ser 'hecho' o 'descartado'")

    result = await db.execute(select(Item).where(Item.id == item_id))
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Item no encontrado")

    item.status = body.status
    item.closed_at = datetime.now(timezone.utc)

    event = ItemEvent(
        item_id=item_id,
        actor=_actor(auth),
        action="closed",
        payload={"status": body.status, "reason": body.reason},
    )
    db.add(event)
    await db.commit()
    await db.refresh(item)
    return item


@router.post("/{item_id}/enrich", status_code=202)
async def enqueue_enrich(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    auth=Depends(api_or_session_user),
):
    from app.jobs.worker import enqueue_job

    result = await db.execute(select(Item).where(Item.id == item_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Item no encontrado")

    run = await enqueue_job(db, kind="enrich", ref_type="item", ref_id=item_id)
    return {"run_id": str(run.id), "status": "encolado"}
