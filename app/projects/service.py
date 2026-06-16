import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.projects.models import Project


class ProjectError(ValueError):
    pass


def _slugify(name: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", name.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug[:60] or "project"


async def list_projects(db: AsyncSession, include_archived: bool = False) -> list[Project]:
    q = select(Project)
    if not include_archived:
        q = q.where(Project.archived_at.is_(None))
    return list((await db.execute(q.order_by(Project.created_at))).scalars().all())


async def get_by_id(db: AsyncSession, project_id: uuid.UUID) -> Project | None:
    return (await db.execute(
        select(Project).where(Project.id == project_id)
    )).scalar_one_or_none()


async def get_by_slug(db: AsyncSession, slug: str) -> Project | None:
    return (await db.execute(
        select(Project).where(Project.slug == slug)
    )).scalar_one_or_none()


async def create_project(
    db: AsyncSession,
    name: str,
    slug: str | None = None,
    description: str | None = None,
    color: str | None = None,
) -> Project:
    name = name.strip()
    if not name:
        raise ProjectError("Project name cannot be empty.")
    final_slug = (slug or _slugify(name)).strip()
    if not final_slug:
        raise ProjectError("Project slug cannot be empty.")
    existing = await get_by_slug(db, final_slug)
    if existing:
        raise ProjectError(f"A project with slug '{final_slug}' already exists.")
    project = Project(name=name, slug=final_slug, description=description, color=color)
    db.add(project)
    await db.flush()
    return project


async def update_project(db: AsyncSession, project: Project, changes: dict) -> Project:
    for field, value in changes.items():
        if hasattr(project, field):
            setattr(project, field, value)
    await db.flush()
    return project


async def archive_project(db: AsyncSession, project: Project) -> Project:
    project.archived_at = datetime.now(timezone.utc)
    await db.flush()
    return project
