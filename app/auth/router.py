import hashlib
import secrets

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import ApiToken, User
from app.auth.service import authenticate
from app.database import get_db
from app.i18n import resolve_lang
from app.i18n import t as _t
from app.templates_config import templates

router = APIRouter(prefix="/auth", tags=["auth"])
setup_router = APIRouter(tags=["setup"])


async def _no_users(db: AsyncSession) -> bool:
    count = await db.scalar(select(func.count()).select_from(User))
    return (count or 0) == 0


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: AsyncSession = Depends(get_db)):
    if request.session.get("user_id"):
        return RedirectResponse("/", status_code=303)
    if await _no_users(db):
        return RedirectResponse("/setup", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = await authenticate(db, email, password)
    if user is None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": _t("login.error_credentials", resolve_lang(request))},
            status_code=401,
        )
    request.session["user_id"] = str(user.id)
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/auth/login", status_code=303)


# ---------- First-run setup ----------

@setup_router.get("/setup", response_class=HTMLResponse, include_in_schema=False)
async def setup_page(request: Request, db: AsyncSession = Depends(get_db)):
    if not await _no_users(db):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "setup.html", {"error": None})


@setup_router.post("/setup", include_in_schema=False)
async def setup_submit(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    project_name: str = Form(...),
    color: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    if not await _no_users(db):
        return RedirectResponse("/", status_code=303)
    if len(password) < 8:
        return templates.TemplateResponse(
            request, "setup.html",
            {"error": _t("setup.error_password_length", resolve_lang(request))},
            status_code=422,
        )
    from app.accounts.service import create_account
    acc, user = await create_account(
        db, name=name, owner_email=email, owner_name=name, password=password, is_superadmin=True
    )
    request.session["user_id"] = str(user.id)

    # Create the first project and a write token in the same transaction.
    from app.projects.service import create_project
    project = await create_project(db, name=project_name, account_id=acc.id, color=color or None)
    raw = secrets.token_urlsafe(32)
    token = ApiToken(
        name="claude-code",
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        scopes="write",
        created_by=user.id,
        project_id=project.id,
    )
    db.add(token)
    await db.commit()

    # Set project in session and flash token once
    request.session["current_project_id"] = str(project.id)
    request.session["current_project_name"] = project.name
    request.session["current_project_slug"] = project.slug
    request.session["current_project_color"] = project.color or "#6366f1"
    request.session["new_token"] = raw
    return RedirectResponse(f"/projects/{project.slug}/settings", status_code=303)
