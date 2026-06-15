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
from app.enums import THREAD_ARTIFACT_KINDS, THREAD_STAGES, check_in

# Re-exportados desde app.enums (única fuente de verdad). Se mantienen aquí por los
# imports existentes (`from app.threads.models import THREAD_STAGES`).
__all__ = ["THREAD_STAGES", "THREAD_ARTIFACT_KINDS", "Thread", "ThreadArtifact", "next_stage", "prev_stage"]

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
        CheckConstraint(check_in("stage", THREAD_STAGES), name="threads_stage_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scope_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scopes.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary_md: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # DB es TEXT (v0005); el ORM debe coincidir (DM-09).
    stage: Mapped[str] = mapped_column(Text, nullable=False, default="idea")
    assignee_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
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
            check_in("kind", THREAD_ARTIFACT_KINDS),
            name="thread_artifacts_kind_check",
        ),
        # DM-05: stage carecía de CHECK; se alinea con threads.stage.
        CheckConstraint(check_in("stage", THREAD_STAGES), name="thread_artifacts_stage_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("threads.id", ondelete="CASCADE"), nullable=False
    )
    # DB es TEXT (v0005); el ORM debe coincidir (DM-09).
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    thread: Mapped["Thread"] = relationship("Thread", back_populates="artifacts")
