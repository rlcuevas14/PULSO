from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from app.auth.deps import current_user_ui
from app.auth.models import User

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(user: User = Depends(current_user_ui)):
    return HTMLResponse(f"<h1>Bienvenido, {user.name}</h1>")
