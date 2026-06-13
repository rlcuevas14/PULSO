"""Lógica de Hilos: CRUD, avance de stage con artefactos, y elaboración IA."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.items.models import Item
from app.scopes.models import Scope
from app.threads.models import THREAD_STAGES, Thread, ThreadArtifact, next_stage, prev_stage

# Mapeo stage → kind de artefacto que produce.
_STAGE_KIND = {
    "investigacion": "investigacion",
    "historias": "historias",
    "spec": "spec",
    "en-desarrollo": "notas",
    "review": "notas",
}


class ThreadError(ValueError):
    """Error de negocio de un hilo."""


async def get_thread(db: AsyncSession, thread_id: uuid.UUID) -> Thread | None:
    return (await db.execute(select(Thread).where(Thread.id == thread_id))).scalar_one_or_none()


async def create_thread(db: AsyncSession, scope_name: str, title: str, summary: str | None) -> Thread:
    scope = (await db.execute(select(Scope).where(Scope.name == scope_name))).scalar_one_or_none()
    if scope is None:
        scope = Scope(name=scope_name, source_repo="hilo")
        db.add(scope)
        await db.flush()
    thread = Thread(scope_id=scope.id, title=title, summary_md=summary, stage="idea")
    db.add(thread)
    await db.flush()
    return thread


async def add_artifact(
    db: AsyncSession, thread: Thread, kind: str, content: str,
    user_id: uuid.UUID | None = None, stage: str | None = None,
) -> ThreadArtifact:
    art = ThreadArtifact(
        thread_id=thread.id, stage=stage or thread.stage, kind=kind,
        content_md=content, created_by_user_id=user_id,
    )
    db.add(art)
    await db.flush()
    return art


async def _open_linked_items(db: AsyncSession, thread: Thread) -> int:
    n = await db.scalar(
        select(func.count()).select_from(Item).where(
            Item.thread_id == thread.id, Item.status.not_in(["hecho", "descartado"])
        )
    )
    return int(n or 0)


async def advance_stage(
    db: AsyncSession, thread: Thread, artifact_content: str | None = None,
    user_id: uuid.UUID | None = None,
) -> Thread:
    """Avanza al siguiente stage. Si se pasa artifact_content, lo guarda para el stage actual."""
    nxt = next_stage(thread.stage)
    if nxt is None:
        raise ThreadError(f"El stage '{thread.stage}' no tiene siguiente.")
    if nxt == "hecho" and await _open_linked_items(db, thread) > 0:
        raise ThreadError("Hay ítems linkeados aún abiertos — ciérralos antes de marcar el hilo hecho.")
    if artifact_content:
        kind = _STAGE_KIND.get(thread.stage, "notas")
        await add_artifact(db, thread, kind, artifact_content, user_id)
    thread.stage = nxt
    await db.flush()
    return thread


async def set_stage(db: AsyncSession, thread: Thread, stage: str) -> Thread:
    """Mueve a un stage arbitrario (retroceder o descartar)."""
    if stage not in THREAD_STAGES:
        raise ThreadError(f"Stage inválido: {stage}")
    thread.stage = stage
    await db.flush()
    return thread


def back_stage_value(stage: str) -> str | None:
    return prev_stage(stage)


async def list_threads(
    db: AsyncSession, stage: str | None = None, scope_name: str | None = None
) -> list[Thread]:
    q = select(Thread)
    if stage:
        q = q.where(Thread.stage == stage)
    if scope_name:
        scope = (await db.execute(select(Scope).where(Scope.name == scope_name))).scalar_one_or_none()
        if scope:
            q = q.where(Thread.scope_id == scope.id)
    return list((await db.execute(q.order_by(Thread.updated_at.desc()))).scalars().all())


async def elaborate_next_stage(db: AsyncSession, thread: Thread) -> dict:
    """Genera con IA un borrador del SIGUIENTE stage a partir de los artefactos previos."""
    from app.ai import llm

    nxt = next_stage(thread.stage)
    if nxt is None or nxt == "hecho":
        raise ThreadError("No hay un stage siguiente para elaborar.")
    arts = (await db.execute(
        select(ThreadArtifact).where(ThreadArtifact.thread_id == thread.id)
        .order_by(ThreadArtifact.created_at)
    )).scalars().all()
    arts_text = "\n\n".join(f"## {a.stage} ({a.kind})\n{a.content_md}" for a in arts)
    try:
        result = await llm.generate_stage(nxt, thread.title, thread.summary_md, arts_text)
    except llm.LLMUnavailable as e:
        raise ThreadError("IA no disponible (sin ANTHROPIC_API_KEY).") from e
    return {"stage": nxt, "content": result["content"], "model": result["model"]}
