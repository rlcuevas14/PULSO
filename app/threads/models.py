"""Development threads: stage containers for heavy features (Sprint 4).

threads.stage is NOT items.status — they are distinct vocabularies that never mix.
Enum values carry no accents (consistent with the repo's item_comments.kind='decision').
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import TIMESTAMP, CheckConstraint, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.enums import THREAD_ARTIFACT_KINDS, THREAD_STAGES, check_in

# Re-exported from app.enums (single source of truth). Kept here for the existing
# imports (`from app.threads.models import THREAD_STAGES`).
__all__ = ["THREAD_STAGES", "THREAD_ARTIFACT_KINDS", "Thread", "ThreadArtifact", "next_stage", "prev_stage"]

# Linear funnel order (for the "next"/"previous" stage).
_FUNNEL = ("idea", "research", "stories", "spec", "in-development", "review", "done")


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
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    scope_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scopes.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary_md: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # The DB column is TEXT (v0005); the ORM must match (DM-09).
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
        # DM-05: stage had no CHECK; aligned with threads.stage.
        CheckConstraint(check_in("stage", THREAD_STAGES), name="thread_artifacts_stage_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("threads.id", ondelete="CASCADE"), nullable=False
    )
    # The DB column is TEXT (v0005); the ORM must match (DM-09).
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    thread: Mapped["Thread"] = relationship("Thread", back_populates="artifacts")
