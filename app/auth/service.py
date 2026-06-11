import hashlib
import secrets
import uuid
from datetime import datetime, timezone

import bcrypt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import ApiToken, User


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(
        select(User).where(User.email == email, User.is_active.is_(True))
    )
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    result = await db.execute(
        select(User).where(User.id == user_id, User.is_active.is_(True))
    )
    return result.scalar_one_or_none()


async def authenticate(db: AsyncSession, email: str, password: str) -> User | None:
    user = await get_user_by_email(db, email)
    if user is None:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


async def create_user(
    db: AsyncSession, email: str, name: str, password: str, role: str = "viewer"
) -> User:
    user = User(email=email, name=name, password_hash=hash_password(password), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def create_api_token(
    db: AsyncSession, name: str, scopes: str, created_by: uuid.UUID
) -> tuple[ApiToken, str]:
    raw = secrets.token_urlsafe(32)
    token = ApiToken(
        name=name,
        token_hash=_hash_token(raw),
        scopes=scopes,
        created_by=created_by,
    )
    db.add(token)
    await db.commit()
    await db.refresh(token)
    return token, raw


async def verify_api_token(db: AsyncSession, raw: str) -> ApiToken | None:
    hashed = _hash_token(raw)
    result = await db.execute(
        select(ApiToken).where(
            ApiToken.token_hash == hashed, ApiToken.revoked_at == None  # noqa: E711
        )
    )
    token = result.scalar_one_or_none()
    if token:
        await db.execute(
            update(ApiToken)
            .where(ApiToken.id == token.id)
            .values(last_used_at=datetime.now(timezone.utc))
        )
        await db.commit()
    return token


async def revoke_api_token(db: AsyncSession, token_id: uuid.UUID) -> None:
    await db.execute(
        update(ApiToken)
        .where(ApiToken.id == token_id)
        .values(revoked_at=datetime.now(timezone.utc))
    )
    await db.commit()
