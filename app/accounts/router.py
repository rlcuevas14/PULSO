import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts import members as ms
from app.accounts import service as acs
from app.auth.deps import require_owner, require_superadmin
from app.auth.models import User
from app.database import get_db
from app.projects import service as ps
from app.templates_config import templates

router = APIRouter(tags=["accounts"])


# ---------- Super-admin: account management ----------

@router.get("/admin/accounts", response_class=HTMLResponse)
async def accounts_admin(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_superadmin),
):
    accounts = await acs.list_accounts(db)
    return templates.TemplateResponse(request, "accounts_admin.html", {
        "user": user, "accounts": accounts, "error": None,
        "new_owner": request.session.pop("new_owner", None),
    })


@router.post("/admin/accounts")
async def accounts_create(
    request: Request,
    name: str = Form(...),
    owner_name: str = Form(...),
    owner_email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_superadmin),
):
    try:
        await acs.create_account(db, name, owner_email, owner_name, password)
    except acs.AccountError as e:
        accounts = await acs.list_accounts(db)
        return templates.TemplateResponse(request, "accounts_admin.html", {
            "user": user, "accounts": accounts, "error": str(e), "new_owner": None,
        }, status_code=422)
    request.session["new_owner"] = owner_email
    return RedirectResponse("/admin/accounts", status_code=303)


@router.post("/admin/accounts/{account_id}/active")
async def accounts_toggle(
    account_id: uuid.UUID,
    active: str = Form("true"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_superadmin),
):
    await acs.set_account_active(db, account_id, active == "true")
    return RedirectResponse("/admin/accounts", status_code=303)


# ---------- Owner: team members + per-project grant matrix ----------

@router.get("/account/members", response_class=HTMLResponse)
async def account_members(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_owner),
):
    members = await ms.list_members(db, user.account_id)
    projects = await ps.list_projects(db, user.account_id, include_archived=False)
    matrix = await ms.member_matrix(db, user.account_id)
    return templates.TemplateResponse(request, "account_members.html", {
        "user": user, "members": members, "projects": projects, "matrix": matrix,
        "error": None, "new_member": request.session.pop("new_member", None),
    })


@router.post("/account/members")
async def account_member_create(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_owner),
):
    try:
        await ms.create_member(db, user.account_id, email, name, password)
    except ms.MemberError as e:
        members = await ms.list_members(db, user.account_id)
        projects = await ps.list_projects(db, user.account_id, include_archived=False)
        matrix = await ms.member_matrix(db, user.account_id)
        return templates.TemplateResponse(request, "account_members.html", {
            "user": user, "members": members, "projects": projects, "matrix": matrix,
            "error": str(e), "new_member": None,
        }, status_code=422)
    request.session["new_member"] = email
    return RedirectResponse("/account/members", status_code=303)


@router.post("/account/members/grant")
async def account_member_grant(
    user_id: uuid.UUID = Form(...),
    project_id: uuid.UUID = Form(...),
    role: str = Form("none"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_owner),
):
    await ms.set_grant(db, user.account_id, user_id, project_id, role)
    return RedirectResponse("/account/members", status_code=303)
