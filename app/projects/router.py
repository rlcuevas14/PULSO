import hashlib
import secrets
import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user_ui, require_admin_strict
from app.auth.models import ApiToken, User
from app.auth.service import revoke_api_token
from app.config import settings
from app.database import get_db
from app.projects import service as ps
from app.templates_config import templates

router = APIRouter(tags=["projects"])


@router.get("/projects", response_class=HTMLResponse)
async def projects_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    projects = await ps.list_projects(db, include_archived=True)
    return templates.TemplateResponse(request, "projects_list.html", {"user": user, "projects": projects})


@router.get("/projects/new", response_class=HTMLResponse)
async def projects_new_page(
    request: Request,
    user: User = Depends(require_admin_strict),
):
    return templates.TemplateResponse(request, "projects_new.html", {"user": user, "error": None})


@router.post("/projects/new")
async def projects_new_submit(
    request: Request,
    name: str = Form(...),
    slug: str = Form(""),
    description: str = Form(""),
    color: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin_strict),
):
    try:
        project = await ps.create_project(
            db,
            name=name,
            slug=slug or None,
            description=description or None,
            color=color or None,
        )
        await db.commit()
    except ps.ProjectError as e:
        return templates.TemplateResponse(
            request, "projects_new.html", {"user": user, "error": str(e)}, status_code=422
        )
    return RedirectResponse(f"/projects/{project.slug}/settings", status_code=303)


@router.get("/projects/{slug}/settings", response_class=HTMLResponse)
async def project_settings(
    slug: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    project = await ps.get_by_slug(db, slug)
    if project is None:
        return Response(status_code=404, content="Project not found")
    tokens = list((await db.execute(
        select(ApiToken).where(
            ApiToken.project_id == project.id,
            ApiToken.revoked_at.is_(None),
        ).order_by(ApiToken.created_at.desc())
    )).scalars().all())
    snippet = (
        f"claude mcp add --transport http {project.slug} {settings.base_url}/mcp \\\n"
        f'  --header "Authorization: Bearer <TOKEN>"'
    )
    return templates.TemplateResponse(request, "projects_settings.html", {
        "user": user, "project": project, "tokens": tokens,
        "snippet": snippet, "new_token": request.session.pop("new_token", None),
    })


@router.post("/projects/{slug}/settings")
async def project_settings_update(
    slug: str,
    name: str = Form(...),
    description: str = Form(""),
    color: str = Form(""),
    repo_url: str = Form(""),
    github_webhook_secret: str = Form(""),
    sentry_client_secret: str = Form(""),
    sentry_api_token: str = Form(""),
    sentry_org: str = Form(""),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin_strict),
):
    project = await ps.get_by_slug(db, slug)
    if project is None:
        return Response(status_code=404, content="Project not found")
    await ps.update_project(db, project, {
        "name": name.strip(),
        "description": description.strip() or None,
        "color": color.strip() or None,
        "repo_url": repo_url.strip() or None,
        "github_webhook_secret": github_webhook_secret.strip() or None,
        "sentry_client_secret": sentry_client_secret.strip() or None,
        "sentry_api_token": sentry_api_token.strip() or None,
        "sentry_org": sentry_org.strip() or None,
    })
    await db.commit()
    return RedirectResponse(f"/projects/{slug}/settings", status_code=303)


@router.post("/projects/{slug}/tokens")
async def project_token_create(
    slug: str,
    request: Request,
    token_name: str = Form(...),
    scopes: str = Form("write"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin_strict),
):
    project = await ps.get_by_slug(db, slug)
    if project is None:
        return Response(status_code=404, content="Project not found")
    raw = secrets.token_urlsafe(32)
    token = ApiToken(
        name=token_name,
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        scopes=scopes,
        created_by=user.id,
        project_id=project.id,
    )
    db.add(token)
    await db.commit()
    # ponytail: show raw token once via session flash, cleared in GET /settings
    request.session["new_token"] = raw
    return RedirectResponse(f"/projects/{slug}/settings", status_code=303)


@router.post("/projects/{slug}/tokens/{token_id}/revoke")
async def project_token_revoke(
    slug: str,
    token_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin_strict),
):
    await revoke_api_token(db, token_id)
    return RedirectResponse(f"/projects/{slug}/settings", status_code=303)


@router.post("/ui/project/switch")
async def switch_project(
    request: Request,
    project_id: str = Form(...),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(current_user_ui),
):
    try:
        pid = uuid.UUID(project_id)
        project = await ps.get_by_id(db, pid)
        if project and not project.archived_at:
            request.session["current_project_id"] = str(project.id)
            request.session["current_project_name"] = project.name
            request.session["current_project_slug"] = project.slug
    except (ValueError, AttributeError):
        pass
    redirect = request.headers.get("referer", "/")
    return RedirectResponse(redirect, status_code=303)
