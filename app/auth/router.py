from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.auth.service import authenticate, create_user
from app.database import get_db
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
            {"error": "Incorrect email or password"},
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
    db: AsyncSession = Depends(get_db),
):
    if not await _no_users(db):
        return RedirectResponse("/", status_code=303)
    if len(password) < 8:
        return templates.TemplateResponse(
            request, "setup.html", {"error": "Password must be at least 8 characters"}, status_code=422
        )
    user = await create_user(db, email=email, name=name, password=password, role="admin")
    request.session["user_id"] = str(user.id)
    return RedirectResponse("/", status_code=303)
