import uuid

import pytest


async def _read_token(client) -> str:
    from app.accounts.service import create_account
    from app.auth.service import create_api_token
    from app.database import get_db
    from app.projects.service import create_project

    s = uuid.uuid4().hex[:8]
    async for db in client.app.dependency_overrides[get_db]():
        acc, owner = await create_account(db, f"a{s}", f"o{s}@t.cl", "O", "passw0rd")
        proj = await create_project(db, name=f"p{s}", account_id=acc.id)
        tok, raw = await create_api_token(db, f"t{s}", "read", owner.id)
        tok.project_id = proj.id
        await db.commit()
        break
    return raw


@pytest.mark.asyncio
async def test_read_token_cannot_create_scope(client):
    """Authorization: a read-scoped token must NOT be able to create an area."""
    raw = await _read_token(client)
    r = await client.post(
        "/api/v1/scopes",
        json={"name": "blocked"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert r.status_code == 403
