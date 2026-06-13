"""Hilos de desarrollo: contenedores de stage para features pesadas (Sprint 4).

threads.stage NO es items.status — son vocabularios distintos que no se cruzan.
Valores de enum sin tilde (coherente con item_comments.kind='decision' del repo).
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import TIMESTAMP, CheckConstraint, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

THREAD_STAGES: tuple[str, ...] = (
    "idea",
    "investigacion",
    "historias",
    "spec",
    "en-desarrollo",
    "review",
    "hecho",
    "descartado",
)

# Orden lineal del funnel (para "siguiente"/"anterior" stage).
_FUNNEL = ("idea", "investigacion", "historias", "spec", "en-desarrollo", "review", "hecho")


def next_stage(stage: str) -> str | None:
    if stage in _FUNNEL:
        i = _FUNNEL.index(stage)
        if i + 1 < len(_FUNNEL):
            return _FUNNEL[i + 1]
    return None


def prev_stage(stage: str) -> str | None:
    if stage in _FUNNEL:
        i = _FUNNEL.index(stage)
        if i - 1 >= 0:
            return _FUNNEL[i - 1]
    return None


class Thread(Base):
    __tablename__ = "threads"
    __table_args__ = (
        CheckConstraint(
            "stage IN ('idea','investigacion','historias','spec',"
            "'en-desarrollo','review','hecho','descartado')",
            name="threads_stage_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("scopes.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary_md: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stage: Mapped[str] = mapped_column(String(20), nullable=False, default="idea")
    assignee_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    artifacts: Mapped[list["ThreadArtifact"]] = relationship(
        "ThreadArtifact", back_populates="thread", order_by="ThreadArtifact.created_at"
    )


class ThreadArtifact(Base):
    __tablename__ = "thread_artifacts"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('investigacion','historias','spec','notas','decision')",
            name="thread_artifacts_kind_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    thread_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("threads.id"), nullable=False)
    stage: Mapped[str] = mapped_column(String(20), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    thread: Mapped["Thread"] = relationship("Thread", back_populates="artifacts")
