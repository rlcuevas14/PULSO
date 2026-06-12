import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.deps import current_user_ui
from app.auth.models import ApiToken, User
from app.database import get_db
from app.items.models import Item
from app.jobs.models import AgentRun
from app.scopes.models import Scope
from app.templates_config import templates

router = APIRouter(tags=["ui"])


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    counts_q = await db.execute(
        select(Item.status, func.count().label("n")).group_by(Item.status)
    )
    counts = {row.status: row.n for row in counts_q}

    recent_q = await db.execute(
        select(Item).order_by(Item.created_at.desc()).limit(10)
    )
    recent = recent_q.scalars().all()

    qw_q = await db.execute(
        select(Item)
        .where(
            Item.impact_ai >= 4,
            Item.effort_ai.in_(["XS", "S"]),
            Item.status.not_in(["hecho", "descartado"]),
        )
        .order_by(Item.impact_ai.desc())
        .limit(5)
    )
    quick_wins = qw_q.scalars().all()

    cost_q = await db.scalar(
        select(func.sum(AgentRun.cost_usd)).where(AgentRun.status == "ok")
    )
    monthly_cost = float(cost_q or 0)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "counts": counts,
            "recent": recent,
            "quick_wins": quick_wins,
            "monthly_cost": monthly_cost,
        },
    )


@router.get("/backlog", response_class=HTMLResponse)
async def backlog(
    request: Request,
    scope: str | None = None,
    status: str | None = None,
    item_type: str | None = None,
    stale: bool | None = None,
    order: str = "reciente",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    q = select(Item)
    if scope:
        scope_row = await db.scalar(select(Scope).where(Scope.name == scope))
        if scope_row:
            q = q.where(Item.scope_id == scope_row.id)
    if status:
        q = q.where(Item.status == status)
    if item_type:
        q = q.where(Item.type == item_type)
    if stale is not None:
        q = q.where(Item.stale_risk == stale)
    if order == "impacto":
        q = q.order_by(Item.impact_ai.desc().nulls_last(), Item.effort_ai)
    else:
        q = q.order_by(Item.created_at.desc())
    q = q.limit(200)

    items_q = await db.execute(q)
    items = items_q.scalars().all()

    scopes_q = await db.execute(
        select(Scope).where(Scope.archived.is_(False)).order_by(Scope.name)
    )
    scopes = scopes_q.scalars().all()
    scope_map = {s.id: s.name for s in scopes}

    ctx = {
        "user": user,
        "items": items,
        "scopes": scopes,
        "scope_map": scope_map,
        "filters": {
            "scope": scope,
            "status": status,
            "type": item_type,
            "stale": stale,
            "order": order,
        },
    }

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "partials/items_table.html", ctx)

    return templates.TemplateResponse(request, "backlog.html", ctx)


@router.get("/items/{item_id}", response_class=HTMLResponse)
async def item_detail(
    item_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    result = await db.execute(
        select(Item)
        .where(Item.id == item_id)
        .options(
            selectinload(Item.comments),
            selectinload(Item.events),
            selectinload(Item.enrichments),
        )
    )
    item = result.scalar_one_or_none()
    if item is None:
        return Response(status_code=404, content="Item no encontrado")

    scope = await db.scalar(select(Scope).where(Scope.id == item.scope_id))

    return templates.TemplateResponse(
        request,
        "item_detail.html",
        {"user": user, "item": item, "scope": scope},
    )


@router.get("/ideas", response_class=HTMLResponse)
async def ideas_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    ideas_q = await db.execute(
        select(Item)
        .where(Item.status == "idea")
        .order_by(Item.created_at.desc())
        .limit(50)
    )
    ideas = ideas_q.scalars().all()

    scopes_q = await db.execute(
        select(Scope).where(Scope.archived.is_(False)).order_by(Scope.name)
    )
    scopes = scopes_q.scalars().all()

    return templates.TemplateResponse(
        request,
        "ideas.html",
        {"user": user, "ideas": ideas, "scopes": scopes},
    )


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    if user.role != "admin":
        return RedirectResponse("/", status_code=303)

    users_q = await db.execute(select(User).order_by(User.created_at))
    users = users_q.scalars().all()

    tokens_q = await db.execute(
        select(ApiToken)
        .where(ApiToken.revoked_at.is_(None))
        .order_by(ApiToken.created_at.desc())
    )
    tokens = tokens_q.scalars().all()

    scopes_q = await db.execute(
        select(Scope).order_by(Scope.display_order, Scope.name)
    )
    scopes = scopes_q.scalars().all()

    runs_q = await db.execute(
        select(AgentRun).order_by(AgentRun.created_at.desc()).limit(20)
    )
    runs = runs_q.scalars().all()

    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "user": user,
            "users": users,
            "tokens": tokens,
            "scopes": scopes,
            "runs": runs,
        },
    )
