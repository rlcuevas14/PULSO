import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings

templates = Jinja2Templates(directory="app/templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.jobs.worker import worker_loop
    task = asyncio.create_task(worker_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def create_app() -> FastAPI:
    from app.auth.router import router as auth_router
    from app.items.router import router as items_router
    from app.scopes.router import router as scopes_router
    from app.ui.router import router as ui_router

    app = FastAPI(title="Pulso — Eduk3", lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="pulso_session",
        https_only=not settings.debug,
    )
    app.include_router(auth_router)
    app.include_router(items_router, prefix="/api/v1")
    app.include_router(scopes_router, prefix="/api/v1")
    app.include_router(ui_router)
    return app


app = create_app()
