import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import TIMESTAMP, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    color: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)
    repo_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Per-project integration secrets (override global env vars when set)
    github_webhook_secret: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sentry_client_secret: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sentry_api_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sentry_org: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    archived_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )
