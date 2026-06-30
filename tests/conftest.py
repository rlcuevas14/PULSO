import os
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.database import Base, get_db
from app.main import create_app

_TEST_DB_URL = os.getenv("TEST_DATABASE_URL", "")


@pytest.fixture(scope="session")
def pg_url() -> str:
    if _TEST_DB_URL:
        yield _TEST_DB_URL
    else:
        with PostgresContainer("pgvector/pgvector:pg16") as pg:
            raw = pg.get_connection_url()
            yield raw.replace("psycopg2", "asyncpg").replace(
                "postgresql://", "postgresql+asyncpg://"
            ).replace("postgresql+asyncpg+asyncpg://", "postgresql+asyncpg://")


@pytest_asyncio.fixture(scope="session")
async def test_engine(pg_url):
    engine = create_async_engine(pg_url, echo=False)
    # Try to create the vector extension — skip if pgvector not installed locally.
    async with engine.connect() as conn:
        try:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.commit()
        except Exception:
            await conn.rollback()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # search_vector es una columna GENERATED que vive solo en la migración v0002
    # (no en el ORM); create_all no la crea. La añadimos aquí para que el full-text
    # funcione en todos los tests (búsqueda, MCP, resolución de relaciones).
    async with engine.begin() as conn:
        await conn.execute(text("""
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='items' AND column_name='search_vector'
                ) THEN
                    ALTER TABLE items ADD COLUMN search_vector tsvector
                    GENERATED ALWAYS AS (
                        setweight(to_tsvector('spanish', coalesce(title, '')), 'A') ||
                        setweight(to_tsvector('spanish', coalesce(summary_md, '')), 'B')
                    ) STORED;
                    CREATE INDEX items_search_gin ON items USING GIN (search_vector);
                END IF;
            END $$;
        """))
    # Truncate all data tables so tests are repeatable across runs. Truncating
    # `accounts` cascades to users, projects, project_members, api_tokens and every
    # project-scoped table (items, scopes, threads, sentry_issues, agent_runs).
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE accounts RESTART IDENTITY CASCADE"))
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db(test_engine) -> AsyncGenerator[AsyncSession, None]:
    TestSession = async_sessionmaker(test_engine, expire_on_commit=False)
    async with TestSession() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(test_engine) -> AsyncGenerator[AsyncClient, None]:
    TestSession = async_sessionmaker(test_engine, expire_on_commit=False)

    async def override_get_db():
        async with TestSession() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.app = app  # expose app for test introspection
        yield ac
