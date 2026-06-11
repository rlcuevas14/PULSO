import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import ApiToken, User
from app.auth.service import get_user_by_id, verify_api_token
from app.database import get_db

_bearer = HTTPBearer(auto_error=False)


async def current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="No autenticado")
    user = await get_user_by_id(db, uuid.UUID(user_id))
    if user is None:
        raise HTTPException(status_code=401, detail="Sesión inválida")
    return user


async def current_user_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Para rutas UI — redirige al login en vez de 401."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=303, headers={"Location": "/auth/login"})
    user = await get_user_by_id(db, uuid.UUID(user_id))
    if user is None:
        raise HTTPException(status_code=303, headers={"Location": "/auth/login"})
    return user


async def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Solo administradores")
    return user


async def api_token_auth(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: AsyncSession = Depends(get_db),
) -> ApiToken:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Token requerido")
    token = await verify_api_token(db, credentials.credentials)
    if token is None:
        raise HTTPException(status_code=401, detail="Token inválido o revocado")
    return token


async def api_or_session_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: AsyncSession = Depends(get_db),
) -> User | ApiToken:
    """Acepta cookie de sesión O Bearer token."""
    if credentials:
        token = await verify_api_token(db, credentials.credentials)
        if token is None:
            raise HTTPException(status_code=401, detail="Token inválido o revocado")
        return token
    user_id = request.session.get("user_id")
    if user_id:
        user = await get_user_by_id(db, uuid.UUID(user_id))
        if user:
            return user
    raise HTTPException(status_code=401, detail="No autenticado")
