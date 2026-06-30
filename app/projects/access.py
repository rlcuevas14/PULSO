"""Single chokepoint for per-project access. Every UI/REST path scopes through here.

Owner ⇒ implicit editor on every project of their account. Members get explicit
`project_members` grants (viewer | editor). Cross-account access is impossible: the
project's `account_id` must match the user's.
"""
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.projects.models import Project, ProjectMember


async def accessible_project_ids(db: AsyncSession, user) -> set[uuid.UUID]:
    if user.account_role == "owner":
        rows = await db.execute(select(Project.id).where(Project.account_id == user.account_id))
        return set(rows.scalars().all())
    rows = await db.execute(
        select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
    )
    return set(rows.scalars().all())


async def user_role_on_project(db: AsyncSession, user, project_id: uuid.UUID) -> str | None:
    """Returns 'editor' | 'viewer' | None (no access). Owner ⇒ 'editor' on own-account projects."""
    proj = await db.get(Project, project_id)
    if proj is None or proj.account_id != user.account_id:
        return None
    if user.account_role == "owner":
        return "editor"
    return await db.scalar(
        select(ProjectMember.role).where(
            ProjectMember.user_id == user.id, ProjectMember.project_id == project_id
        )
    )


async def require_project_access(
    db: AsyncSession, user, project_id: uuid.UUID, *, need_write: bool = False
) -> None:
    role = await user_role_on_project(db, user, project_id)
    if role is None:
        raise HTTPException(status_code=403, detail="No access to this project")
    if need_write and role == "viewer":
        raise HTTPException(status_code=403, detail="Viewer cannot write to this project")
