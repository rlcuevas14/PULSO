import uuid

import pytest

from app.accounts.models import Account
from app.auth.models import User
from app.auth.service import hash_password
from app.projects.models import Project, ProjectMember


@pytest.mark.asyncio
async def test_account_owns_project_and_member(db):
    acc = Account(name="Acme", slug=f"acme-{uuid.uuid4().hex[:6]}")
    db.add(acc)
    await db.flush()
    owner = User(
        email=f"o{uuid.uuid4().hex[:6]}@t.cl",
        name="O",
        password_hash=hash_password("x"),
        account_id=acc.id,
        account_role="owner",
    )
    db.add(owner)
    await db.flush()
    proj = Project(name="Web", slug="web", account_id=acc.id)
    db.add(proj)
    await db.flush()
    db.add(ProjectMember(user_id=owner.id, project_id=proj.id, role="editor"))
    await db.commit()
    assert proj.account_id == acc.id
    assert owner.account_role == "owner"
    assert owner.is_superadmin is False


@pytest.mark.asyncio
async def test_create_account_makes_owner(db):
    from app.accounts.service import create_account

    acc, owner = await create_account(
        db, "Acme", f"boss{uuid.uuid4().hex[:6]}@acme.cl", "Boss", "supersecret"
    )
    assert owner.account_id == acc.id
    assert owner.account_role == "owner"
    assert owner.is_superadmin is False
    assert acc.slug  # auto-generated
