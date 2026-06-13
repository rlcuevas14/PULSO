"""Router REST de Hilos (JSON). La UI vive en app/ui/router.py."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import api_or_session_user
from app.database import get_db
from app.threads import service
from app.threads.models import ThreadArtifact

router = APIRouter(prefix="/threads", tags=["threads"])


class ThreadCreate(BaseModel):
    scope_name: str
    title: str
    summary: str | None = None


class AdvanceBody(BaseModel):
    artifact_content: str | None = None


class StageBody(BaseModel):
    stage: str


class ArtifactBody(BaseModel):
    kind: str
    content: str


def _thread_out(t) -> dict:
    return {
        "id": str(t.id), "title": t.title, "summary_md": t.summary_md,
        "stage": t.stage, "scope_id": str(t.scope_id),
    }


@router.post("", status_code=201)
async def create_thread(
    body: ThreadCreate, db: AsyncSession = Depends(get_db), _auth=Depends(api_or_session_user)
):
    t = await service.create_thread(db, body.scope_name, body.title, body.summary)
    await db.commit()
    return _thread_out(t)


@router.get("")
async def list_threads(
    stage: str | None = Query(None),
    scope: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _auth=Depends(api_or_session_user),
):
    threads = await service.list_threads(db, stage, scope)
    return [_thread_out(t) for t in threads]


@router.get("/{thread_id}")
async def get_thread(
    thread_id: uuid.UUID, db: AsyncSession = Depends(get_db), _auth=Depends(api_or_session_user)
):
    t = await service.get_thread(db, thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Hilo no encontrado")
    arts = (await db.execute(
        select(ThreadArtifact).where(ThreadArtifact.thread_id == thread_id)
        .order_by(ThreadArtifact.created_at)
    )).scalars().all()
    return {
        **_thread_out(t),
        "artifacts": [
            {"id": str(a.id), "stage": a.stage, "kind": a.kind, "content_md": a.content_md,
             "created_at": a.created_at.isoformat()} for a in arts
        ],
    }


@router.post("/{thread_id}/advance")
async def advance(
    thread_id: uuid.UUID, body: AdvanceBody,
    db: AsyncSession = Depends(get_db), _auth=Depends(api_or_session_user),
):
    t = await service.get_thread(db, thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Hilo no encontrado")
    try:
        await service.advance_stage(db, t, body.artifact_content)
    except service.ThreadError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    await db.commit()
    return _thread_out(t)


@router.post("/{thread_id}/stage")
async def set_stage(
    thread_id: uuid.UUID, body: StageBody,
    db: AsyncSession = Depends(get_db), _auth=Depends(api_or_session_user),
):
    t = await service.get_thread(db, thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Hilo no encontrado")
    try:
        await service.set_stage(db, t, body.stage)
    except service.ThreadError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    await db.commit()
    return _thread_out(t)


@router.post("/{thread_id}/artifacts", status_code=201)
async def add_artifact(
    thread_id: uuid.UUID, body: ArtifactBody,
    db: AsyncSession = Depends(get_db), _auth=Depends(api_or_session_user),
):
    t = await service.get_thread(db, thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Hilo no encontrado")
    art = await service.add_artifact(db, t, body.kind, body.content)
    await db.commit()
    return {"id": str(art.id), "stage": art.stage, "kind": art.kind}


@router.post("/{thread_id}/elaborate-stage")
async def elaborate_stage(
    thread_id: uuid.UUID, db: AsyncSession = Depends(get_db), _auth=Depends(api_or_session_user)
):
    t = await service.get_thread(db, thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Hilo no encontrado")
    try:
        draft = await service.elaborate_next_stage(db, t)
    except service.ThreadError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return draft
