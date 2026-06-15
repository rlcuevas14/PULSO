"""ORM de sentry_issues (la tabla existe desde v0001; aquí el modelo para ORM/tests)."""

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, TIMESTAMP, CheckConstraint, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.enums import SENTRY_LEVELS, SENTRY_STATUSES, SENTRY_TRIAGE, check_in


class SentryIssue(Base):
    __tablename__ = "sentry_issues"
    __table_args__ = (
        CheckConstraint(check_in("level", SENTRY_LEVELS), name="sentry_issues_level_check"),
        CheckConstraint(
            f"triage IS NULL OR {check_in('triage', SENTRY_TRIAGE)}",
            name="sentry_issues_triage_check",
        ),
        CheckConstraint(
            check_in("status", SENTRY_STATUSES), name="sentry_issues_status_check"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sentry_issue_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    project: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    level: Mapped[str] = mapped_column(String(10), nullable=False, default="error")
    triage: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new")
    first_seen: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    last_seen: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    events_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    item_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )
