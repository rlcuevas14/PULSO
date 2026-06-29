# Multi-account / multi-user Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Pulso multi-tenant — an account is an isolated set of projects; users belong to one account; owners grant collaborators per-project viewer/editor access; MCP token scope ≤ the minter's role.

**Architecture:** Approach A — account sits on top of the existing project layer. New `accounts` table + `account_id` on `users`/`projects` + a `project_members` grant matrix. Domain tables (items/scopes/threads/sentry/agent_runs) are untouched — they inherit the account via `project_id`. Isolation is enforced through one chokepoint (`app/projects/access.py`).

**Tech Stack:** FastAPI, SQLAlchemy async (asyncpg), Alembic, Jinja2 + HTMX, hand-rolled MCP (JSON-RPC). Postgres + pgvector.

## Global Constraints

- English everywhere: enum values, error messages, MCP tool names, UI copy. (Thread stages stay Spanish — out of scope.)
- Every mutation emits an `ItemEvent` (audit primitive) — unchanged here; this feature touches auth/projects, not item mutations.
- Enums are the single source of truth and mirror DB CHECK constraints; adding a value requires a migration.
- Tests run via `Base.metadata.create_all` (ORM-driven), not Alembic. New tables/columns appear in tests through the models. The Alembic migration is for production only and must be kept in sync with the models.
- LLM always via `app/ai/llm.py`; degrades without API key. (Not touched here.)
- Migration head is `v0011`; new migration is `v0012`. Migrations use raw SQL via `op.execute`.
- Coverage gate: CI must pass `pytest --cov=app --cov-fail-under=90`.
- Commit per task. Branch: `feat/multi-account`.

---

### Task 1: Enums + data model (accounts, project_members, user/project columns)

**Files:**
- Modify: `app/enums.py` (add account/grant role tuples)
- Create: `app/accounts/__init__.py`, `app/accounts/models.py` (Account)
- Modify: `app/auth/models.py` (User: +account_id, +account_role, +is_superadmin)
- Modify: `app/projects/models.py` (Project: +account_id, slug unique→(account_id,slug); +ProjectMember)
- Test: `tests/test_accounts.py`

**Interfaces:**
- Produces: `Account` (id, name, slug, is_active, created_at, updated_at); `ProjectMember` (user_id, project_id, role); `User.account_id`, `User.account_role`, `User.is_superadmin`, `Project.account_id`.
- Enums: `ACCOUNT_ROLES = ("owner", "member")`, `PROJECT_MEMBER_ROLES = ("viewer", "editor")`.

- [ ] **Step 1: Add enums** to `app/enums.py` after the `# --- auth ---` block:
```python
ACCOUNT_ROLES: tuple[str, ...] = ("owner", "member")
PROJECT_MEMBER_ROLES: tuple[str, ...] = ("viewer", "editor")
```

- [ ] **Step 2: Account model** `app/accounts/models.py`:
```python
import uuid
from datetime import datetime
from sqlalchemy import TIMESTAMP, Boolean, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```
`app/accounts/__init__.py`: empty.

- [ ] **Step 3: User columns** in `app/auth/models.py` — add import `from app.enums import ACCOUNT_ROLES, TOKEN_SCOPES, USER_ROLES, check_in`, add CheckConstraint for account_role, and columns:
```python
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    account_role: Mapped[str] = mapped_column(String(20), nullable=False, default="member")
    is_superadmin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
```
Add to `__table_args__`: `CheckConstraint(check_in("account_role", ACCOUNT_ROLES), name="users_account_role_check")`. Keep existing `role` column for now (dropped in migration; removing from the model now would break create_all parity with rows — instead set `role` default and ignore it; we drop it from the model in Task 10's note). **Decision:** remove `role` from the model now and from the v0012 migration together — tests create_all fresh so no parity issue. Drop the `users_role_check` constraint line and the `role` column line.

- [ ] **Step 4: Project columns + ProjectMember** in `app/projects/models.py`:
```python
from sqlalchemy import TIMESTAMP, ForeignKey, String, Text, UniqueConstraint, func, CheckConstraint
from app.enums import PROJECT_MEMBER_ROLES, check_in
# Project: add account_id, drop unique=True on slug, add table_args unique(account_id, slug)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    __table_args__ = (UniqueConstraint("account_id", "slug", name="projects_account_slug_uniq"),)
# slug column: remove `unique=True`


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint("user_id", "project_id", name="project_members_uniq"),
        CheckConstraint(check_in("role", PROJECT_MEMBER_ROLES), name="project_members_role_check"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="editor")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
```
Ensure `app/main.py` imports the new models so `Base.metadata` sees them (models are imported via routers; add `from app.accounts import models as _acc  # noqa` and ProjectMember is in projects.models already imported). Verify create_all includes them.

- [ ] **Step 5: Write failing test** `tests/test_accounts.py`:
```python
import uuid
import pytest
from app.accounts.models import Account
from app.auth.models import User
from app.projects.models import Project, ProjectMember
from app.auth.service import hash_password


@pytest.mark.asyncio
async def test_account_owns_project_and_member(db):
    acc = Account(name="Acme", slug=f"acme-{uuid.uuid4().hex[:6]}")
    db.add(acc); await db.flush()
    owner = User(email=f"o{uuid.uuid4().hex[:6]}@t.cl", name="O",
                 password_hash=hash_password("x"), account_id=acc.id, account_role="owner")
    db.add(owner); await db.flush()
    proj = Project(name="Web", slug="web", account_id=acc.id)
    db.add(proj); await db.flush()
    db.add(ProjectMember(user_id=owner.id, project_id=proj.id, role="editor"))
    await db.commit()
    assert proj.account_id == acc.id
```

- [ ] **Step 6: Run test** `pytest tests/test_accounts.py -v` → PASS once models/enums are in place.
- [ ] **Step 7: ruff + mypy** `ruff check app/ tests/ && mypy app/` → clean.
- [ ] **Step 8: Commit** `git commit -m "feat(accounts): account + project_members data model, account columns on users/projects"`

---

### Task 2: Account service (create_account, reusable for future signup)

**Files:**
- Create: `app/accounts/service.py`
- Test: `tests/test_accounts.py` (extend)

**Interfaces:**
- Produces: `create_account(db, name, owner_email, owner_name, password, *, is_superadmin=False) -> tuple[Account, User]`; `list_accounts(db) -> list[Account]`; `set_account_active(db, account_id, active) -> None`; `_slugify(name) -> str` (reuse projects' slugify pattern); `AccountError(Exception)`.

- [ ] **Step 1: Failing test** in `tests/test_accounts.py`:
```python
@pytest.mark.asyncio
async def test_create_account_makes_owner(db):
    from app.accounts.service import create_account
    acc, owner = await create_account(db, "Acme", "boss@acme.cl", "Boss", "supersecret")
    assert owner.account_id == acc.id
    assert owner.account_role == "owner"
    assert owner.is_superadmin is False
    assert acc.slug  # auto-generated
```

- [ ] **Step 2: Run** `pytest tests/test_accounts.py::test_create_account_makes_owner -v` → FAIL (no create_account).

- [ ] **Step 3: Implement** `app/accounts/service.py`:
```python
import re
import uuid
import unicodedata
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.accounts.models import Account
from app.auth.models import User
from app.auth.service import hash_password


class AccountError(Exception):
    pass


def _slugify(name: str) -> str:
    norm = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", norm.lower()).strip("-")
    return slug or "account"


async def _unique_slug(db: AsyncSession, base: str) -> str:
    slug = base
    while await db.scalar(select(Account.id).where(Account.slug == slug)):
        slug = f"{base}-{uuid.uuid4().hex[:4]}"
    return slug


async def create_account(db, name, owner_email, owner_name, password, *, is_superadmin=False):
    name = name.strip()
    if not name:
        raise AccountError("Account name cannot be empty.")
    if await db.scalar(select(User.id).where(User.email == owner_email)):
        raise AccountError("A user with that email already exists.")
    acc = Account(name=name, slug=await _unique_slug(db, _slugify(name)))
    db.add(acc); await db.flush()
    owner = User(email=owner_email, name=owner_name, password_hash=hash_password(password),
                 account_id=acc.id, account_role="owner", is_superadmin=is_superadmin)
    db.add(owner); await db.commit()
    await db.refresh(acc); await db.refresh(owner)
    return acc, owner


async def list_accounts(db):
    return list((await db.execute(select(Account).order_by(Account.created_at.desc()))).scalars().all())


async def set_account_active(db, account_id, active):
    acc = await db.get(Account, account_id)
    if acc:
        acc.is_active = active
        await db.commit()
```

- [ ] **Step 4: Run** test → PASS.
- [ ] **Step 5: ruff + mypy** → clean.
- [ ] **Step 6: Commit** `git commit -m "feat(accounts): create_account service (reusable for future public signup)"`

---

### Task 3: Access chokepoint (`app/projects/access.py`)

**Files:**
- Create: `app/projects/access.py`
- Test: `tests/test_access.py`

**Interfaces:**
- Produces:
  - `async def accessible_project_ids(db, user) -> set[uuid.UUID]`
  - `async def user_role_on_project(db, user, project_id) -> str | None`  (returns "editor"/"viewer"/None; owner ⇒ "editor")
  - `async def require_project_access(db, user, project_id, *, need_write=False) -> None`  (raises HTTPException 403)
- Consumes: `User` (account_id, account_role), `Project`, `ProjectMember`.

- [ ] **Step 1: Failing test** `tests/test_access.py`:
```python
import uuid
import pytest
from fastapi import HTTPException
from app.accounts.service import create_account
from app.projects.service import create_project
from app.projects.models import ProjectMember
from app.projects.access import accessible_project_ids, user_role_on_project, require_project_access


async def _setup(db):
    acc, owner = await create_account(db, "A", f"o{uuid.uuid4().hex[:6]}@t.cl", "O", "pw")
    p1 = await create_project(db, name="P1", account_id=acc.id)
    p2 = await create_project(db, name="P2", account_id=acc.id)
    await db.commit()
    return acc, owner, p1, p2


@pytest.mark.asyncio
async def test_owner_sees_all_account_projects(db):
    acc, owner, p1, p2 = await _setup(db)
    ids = await accessible_project_ids(db, owner)
    assert {p1.id, p2.id} <= ids
    assert await user_role_on_project(db, owner, p1.id) == "editor"


@pytest.mark.asyncio
async def test_member_sees_only_granted(db):
    acc, owner, p1, p2 = await _setup(db)
    from app.auth.service import hash_password
    from app.auth.models import User
    m = User(email=f"m{uuid.uuid4().hex[:6]}@t.cl", name="M", password_hash=hash_password("pw"),
             account_id=acc.id, account_role="member")
    db.add(m); await db.flush()
    db.add(ProjectMember(user_id=m.id, project_id=p1.id, role="viewer"))
    await db.commit()
    assert await accessible_project_ids(db, m) == {p1.id}
    assert await user_role_on_project(db, m, p2.id) is None
    with pytest.raises(HTTPException):
        await require_project_access(db, m, p1.id, need_write=True)  # viewer can't write
    await require_project_access(db, m, p1.id)  # read ok, no raise
```

- [ ] **Step 2: Run** → FAIL (module missing). Note: `create_project` must accept `account_id` (added in Task 4 Step 0 below — reorder: implement `create_project(account_id=...)` change first if running standalone). For plan order, Task 4's project-service change lands before this test passes; run this test at the end of Task 4.

- [ ] **Step 3: Implement** `app/projects/access.py`:
```python
import uuid
from fastapi import HTTPException
from sqlalchemy import select
from app.projects.models import Project, ProjectMember


async def accessible_project_ids(db, user) -> set[uuid.UUID]:
    if user.account_role == "owner":
        rows = await db.execute(select(Project.id).where(Project.account_id == user.account_id))
        return set(rows.scalars().all())
    rows = await db.execute(select(ProjectMember.project_id).where(ProjectMember.user_id == user.id))
    return set(rows.scalars().all())


async def user_role_on_project(db, user, project_id) -> str | None:
    proj = await db.get(Project, project_id)
    if proj is None or proj.account_id != user.account_id:
        return None
    if user.account_role == "owner":
        return "editor"
    return await db.scalar(
        select(ProjectMember.role).where(
            ProjectMember.user_id == user.id, ProjectMember.project_id == project_id
        )
    )


async def require_project_access(db, user, project_id, *, need_write=False) -> None:
    role = await user_role_on_project(db, user, project_id)
    if role is None:
        raise HTTPException(status_code=403, detail="No access to this project")
    if need_write and role == "viewer":
        raise HTTPException(status_code=403, detail="Viewer cannot write to this project")
```

- [ ] **Step 4: Commit** (after Task 4) `git commit -m "feat(access): per-project access chokepoint (owner/editor/viewer)"`

---

### Task 4: Project service account-scoping

**Files:**
- Modify: `app/projects/service.py` (`create_project` takes `account_id`; `list_projects`, `get_by_slug` take an optional `account_id` filter)
- Test: covered by Task 3 + existing project tests

**Interfaces:**
- Produces: `create_project(db, name, account_id, slug=None, description=None, color=None) -> Project`; `list_projects(db, account_id, include_archived=False)`; `get_by_slug(db, slug, account_id)`.

- [ ] **Step 1: Modify `create_project`** — add required `account_id: uuid.UUID` param, set on the Project, and make slug-uniqueness scoped to the account (query existing slug within account). 
- [ ] **Step 2: Modify `list_projects`/`get_by_slug`/`get_by_id`** to accept and filter by `account_id` (callers pass `user.account_id`). `get_by_id` additionally checks the project's account matches.
- [ ] **Step 3: Update all callers** (`auth/router.py` setup, `projects/router.py`, `ui/router.py`) to pass `account_id`. (Token/UI wiring finalized in Tasks 6–9; here just make signatures compile and existing call sites pass `user.account_id`.)
- [ ] **Step 4: Run** `pytest tests/test_access.py tests/test_accounts.py -v` → PASS. ruff + mypy clean.
- [ ] **Step 5: Commit** `git commit -m "feat(projects): scope project service by account"` (+ commit Task 3's access.py here).

---

### Task 5: Auth deps — owner / superadmin guards

**Files:**
- Modify: `app/auth/deps.py`
- Test: `tests/test_auth.py` (extend)

**Interfaces:**
- Produces: `require_owner(user) -> User` (account_role == "owner", session only); `require_superadmin(user) -> User` (is_superadmin). Replace `require_admin_strict` usages with `require_owner`. Keep `require_admin`/`require_admin_strict` as thin aliases to avoid breaking other imports, or update all imports.

- [ ] **Step 1: Failing test** in `tests/test_auth.py`: a member session hitting an owner-only action gets 403; owner passes; non-superadmin hitting `/admin/accounts` gets 403.
- [ ] **Step 2: Implement** in `deps.py`:
```python
async def require_owner(auth = Depends(api_or_session_user)) -> User:
    if not isinstance(auth, User):
        raise HTTPException(status_code=403, detail="Owner session required")
    if auth.account_role != "owner":
        raise HTTPException(status_code=403, detail="Owner only")
    return auth


async def require_superadmin(user: User = Depends(current_user)) -> User:
    if not user.is_superadmin:
        raise HTTPException(status_code=403, detail="Superadmin only")
    return user
```
Update `require_admin`/`require_admin_strict` → keep name `require_owner`; replace `role != "admin"` logic. Grep-replace `require_admin_strict` imports in `projects/router.py` (token/settings tasks will refine).
- [ ] **Step 3: Run** the auth tests → PASS. ruff + mypy clean.
- [ ] **Step 4: Commit** `git commit -m "feat(auth): require_owner + require_superadmin guards"`

---

### Task 6: Setup wizard → first account + superadmin owner

**Files:**
- Modify: `app/auth/router.py` (`setup_submit`)
- Test: `tests/test_auth.py`

- [ ] **Step 1: Failing test**: POST `/setup` with account/owner/project fields on an empty DB creates an Account, an owner User with `is_superadmin=True`, a Project under that account, and a write token; session has `user_id`.
- [ ] **Step 2: Implement** — replace `create_user(...role="admin")` block with:
```python
from app.accounts.service import create_account
acc, user = await create_account(db, name=project_name + " account" if not account_name else account_name,
                                 owner_email=email, owner_name=name, password=password, is_superadmin=True)
request.session["user_id"] = str(user.id)
project = await create_project(db, name=project_name, account_id=acc.id)
# token creation unchanged (project_id=project.id, scopes="write")
```
Add an optional `account_name: str = Form("")` field; default the account name from the project or owner name. Update `setup.html` to include an "Account name" input (optional).
- [ ] **Step 3: Run** test → PASS. ruff + mypy clean.
- [ ] **Step 4: Commit** `git commit -m "feat(setup): first-run creates account + superadmin owner"`

---

### Task 7: Super-admin accounts UI (`/admin/accounts`)

**Files:**
- Create: `app/accounts/router.py` (mounted in `app/main.py`)
- Create: `app/templates/accounts_admin.html`
- Test: `tests/test_accounts.py`

**Interfaces:**
- Routes: `GET /admin/accounts` (list + create form, `require_superadmin`), `POST /admin/accounts` (create_account, flash temp password once), `POST /admin/accounts/{id}/active` (toggle).

- [ ] **Step 1: Failing test**: superadmin GET `/admin/accounts` 200 + lists; POST creates account+owner; a non-superadmin user gets 403.
- [ ] **Step 2: Implement** router using `create_account`/`list_accounts`/`set_account_active`; on `AccountError` re-render with 422. Mount in `main.py`: `app.include_router(accounts_router)`.
- [ ] **Step 3: Template** `accounts_admin.html` follows `projects_list.html` structure: a table of accounts (name, slug, active, created) + a "Create account" form (account name, owner name, owner email, temp password). Show created owner credentials once via session flash.
- [ ] **Step 4: Run** test → PASS. ruff + mypy clean.
- [ ] **Step 5: Commit** `git commit -m "feat(admin): superadmin accounts management UI"`

---

### Task 8: Owner member matrix (`/account/members`)

**Files:**
- Create: `app/accounts/members_service.py` (or extend `app/projects/service.py`)
- Modify: `app/accounts/router.py` (owner routes) or new `app/accounts/members_router.py`
- Create: `app/templates/account_members.html`
- Test: `tests/test_members.py`

**Interfaces:**
- Produces: `create_member(db, account_id, email, name, password) -> User`; `list_members(db, account_id) -> list[User]`; `set_grant(db, user_id, project_id, role|None) -> None` (None deletes the grant); `member_matrix(db, account_id) -> {user_id: {project_id: role}}`.
- Routes: `GET /account/members` (require_owner) renders the matrix; `POST /account/members` creates a collaborator; `POST /account/members/grant` sets a single cell (user_id, project_id, role∈{none,viewer,editor}).

- [ ] **Step 1: Failing tests** `tests/test_members.py`: owner creates a member (account_role="member", same account); owner sets a grant → ProjectMember row; setting role "none" removes it; a member cannot reach `/account/members` (403); created member can log in.
- [ ] **Step 2: Implement service** — `create_member` mirrors `create_account`'s user creation but `account_role="member"` and `account_id` fixed to the owner's account; `set_grant` upserts/deletes `ProjectMember`; enforce target user and project belong to the owner's account (else 403/skip).
- [ ] **Step 3: Implement routes + template** — matrix grid: rows=members, cols=projects (from `accessible_project_ids(owner)` = all account projects), each cell a 3-option `<select>` posting to `/account/members/grant` via HTMX. Create-collaborator form at top. Flash temp password once.
- [ ] **Step 4: Run** tests → PASS. ruff + mypy clean.
- [ ] **Step 5: Commit** `git commit -m "feat(members): owner member matrix + per-project grants"`

---

### Task 9: Token mint — scope ≤ role; access on project routes

**Files:**
- Modify: `app/projects/router.py` (`project_token_create`, `project_settings`, `project_settings_update`, `project_token_revoke`, `switch_project`, `projects_list`)
- Modify: `app/auth/router.py` token_created template already uses `base_url` (done earlier)
- Test: `tests/test_error_paths.py` (token rules) + `tests/test_members.py`

**Interfaces:**
- `project_token_create`: dep `current_user` (session); compute `role = user_role_on_project(db, user, project.id)`; if `role is None` → 403; allowed scopes: editor→{read,write}, viewer→{read}; reject `scopes="write"` for viewer with 403; owner→both.
- `projects_list`: filter to `accessible_project_ids`. `project_settings`/`update`/`token_revoke`/`switch_project`: `require_project_access` (update/token need_write=True semantics: settings edit = owner-or-editor).
- `get_by_slug` calls pass `user.account_id`.

- [ ] **Step 1: Failing tests**: viewer-granted member minting a `write` token → 403; editor minting `write` → 200; minting a token for a project in another account → 403; owner lists only own-account projects.
- [ ] **Step 2: Implement** the dep + scope checks; swap `require_admin_strict` → `current_user` + `require_project_access`/`user_role_on_project` as above; scope projects_list by `accessible_project_ids`.
- [ ] **Step 3: Run** tests → PASS. ruff + mypy clean.
- [ ] **Step 4: Commit** `git commit -m "feat(tokens): scope<=role mint rule + project route access enforcement"`

---

### Task 10: UI/REST screen enforcement + MCP sanity

**Files:**
- Modify: `app/ui/router.py` (screens `/`, `/backlog`, `/priority`, `/threads`, `/incidents`, `/items/{id}`, `/admin`, and `/ui/...` actions) to constrain the current project to `accessible_project_ids`; the navbar selector lists only accessible projects.
- Modify: REST routers under items/scopes/threads/webhooks where a logged-in user (not token) reads/writes — gate by `require_project_access`.
- Test: `tests/test_isolation.py`

**Interfaces:** Consumes `accessible_project_ids`, `require_project_access`, `user_role_on_project`.

- [ ] **Step 1: Failing test** `tests/test_isolation.py`: two accounts A and B, each with a project + an item. User of A cannot GET B's item page (404/403), cannot switch to B's project, B's project absent from A's selector; MCP token of A's project cannot read B's items (already isolated — assert it stays so).
- [ ] **Step 2: Implement** — wherever the UI resolves `current_project_id` from session, validate membership in `accessible_project_ids`; if invalid, fall back to the first accessible project (or empty state). Single-item/thread/incident pages: load the row, then `require_project_access(db, user, row.project_id)`.
- [ ] **Step 3: Run** test → PASS. ruff + mypy clean.
- [ ] **Step 4: Commit** `git commit -m "feat(ui): account isolation across screens + REST"`

---

### Task 11: Migration v0012 (production)

**Files:**
- Create: `migrations/versions/v0012_accounts.py`
- Modify: `app/config.py` (add `default_account_name: str = "Default"`)

- [ ] **Step 1: Write migration** `migrations/versions/v0012_accounts.py`:
```python
"""v0012: accounts + project_members + account columns; migrate existing data to one account"""
import os
from alembic import op

revision = "v0012"
down_revision = "v0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE accounts (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE project_members (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            role TEXT NOT NULL DEFAULT 'editor' CHECK (role IN ('viewer','editor')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(user_id, project_id)
        )
    """)
    # user columns
    op.execute("ALTER TABLE users ADD COLUMN account_id uuid REFERENCES accounts(id) ON DELETE CASCADE")
    op.execute("ALTER TABLE users ADD COLUMN account_role TEXT NOT NULL DEFAULT 'member' "
               "CHECK (account_role IN ('owner','member'))")
    op.execute("ALTER TABLE users ADD COLUMN is_superadmin BOOLEAN NOT NULL DEFAULT false")
    # project columns
    op.execute("ALTER TABLE projects ADD COLUMN account_id uuid REFERENCES accounts(id) ON DELETE CASCADE")

    # Backfill: if any users or projects exist, fold them into one default account.
    default_name = os.getenv("DEFAULT_ACCOUNT_NAME", "Default")
    op.execute(f"""
        DO $$
        DECLARE acc uuid; first_user uuid;
        BEGIN
            IF EXISTS (SELECT 1 FROM users) OR EXISTS (SELECT 1 FROM projects) THEN
                INSERT INTO accounts (name, slug) VALUES ('{default_name}', 'default')
                  RETURNING id INTO acc;
                UPDATE projects SET account_id = acc WHERE account_id IS NULL;
                UPDATE users SET account_id = acc WHERE account_id IS NULL;
                -- earliest admin (or earliest user) becomes owner + superadmin
                SELECT id INTO first_user FROM users
                  ORDER BY (role = 'admin') DESC, created_at ASC LIMIT 1;
                UPDATE users SET account_role = 'owner', is_superadmin = true WHERE id = first_user;
            END IF;
        END $$;
    """)
    # now enforce NOT NULL + swap slug uniqueness + drop legacy role
    op.execute("ALTER TABLE users ALTER COLUMN account_id SET NOT NULL")
    op.execute("ALTER TABLE projects ALTER COLUMN account_id SET NOT NULL")
    op.execute("ALTER TABLE projects DROP CONSTRAINT IF EXISTS projects_slug_key")
    op.execute("CREATE UNIQUE INDEX projects_account_slug_uniq ON projects(account_id, slug)")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS role")
    op.execute("CREATE INDEX project_members_user ON project_members(user_id)")
    op.execute("CREATE INDEX project_members_project ON project_members(project_id)")


def downgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT")
    op.execute("DROP INDEX IF EXISTS projects_account_slug_uniq")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS account_id")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS is_superadmin")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS account_role")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS account_id")
    op.execute("DROP TABLE IF EXISTS project_members")
    op.execute("DROP TABLE IF EXISTS accounts")
```

- [ ] **Step 2: Round-trip test locally** against a copy: `alembic upgrade head` then `alembic downgrade -1` then `upgrade head` (CI also runs the round-trip). Expected: no error.
- [ ] **Step 3: Commit** `git commit -m "feat(migration): v0012 accounts + backfill existing data to one account"`

---

### Task 12: Test modernization + isolation/grant coverage + conftest + 90% gate

**Files:**
- Modify: `tests/conftest.py` (TRUNCATE list), `tests/test_mcp.py`, `tests/test_error_paths.py`, `tests/test_sprint4.py` (fixtures + names + args + assertions)
- Modify: `.github/workflows/ci.yml` (add `--cov=app --cov-fail-under=90`)
- Create: any missing isolation/grant tests (Tasks 3/8/9/10 already add most)

- [ ] **Step 1: conftest TRUNCATE** — change line 62 to:
```python
await conn.execute(text("TRUNCATE project_members, api_tokens, users, projects, accounts RESTART IDENTITY CASCADE"))
```
- [ ] **Step 2: Fix `_token` helpers** in `tests/test_mcp.py` and `tests/test_error_paths.py` to create account + project + project-scoped token:
```python
async def _token(client, scopes="write"):
    from app.accounts.service import create_account
    from app.projects.service import create_project
    from app.auth.service import create_api_token
    from app.database import get_db
    suffix = uuid.uuid4().hex[:8]
    async for db in client.app.dependency_overrides[get_db]():
        acc, owner = await create_account(db, f"acc-{suffix}", f"o{suffix}@t.cl", "O", "pw")
        project = await create_project(db, name=f"proj-{suffix}", account_id=acc.id)
        tok, raw = await create_api_token(db, f"t-{suffix}", scopes, owner.id)
        tok.project_id = project.id
        await db.commit()
        break
    return raw
```
- [ ] **Step 3: Settle stale drift** — in the three test files: `pulso_crear→pulso_create`, `pulso_completar→pulso_complete`, `pulso_contexto→pulso_context`, `pulso_buscar→pulso_search`, `pulso_incidente→pulso_incident`, `pulso_incidentes→pulso_incidents`, `pulso_incidente_resolver→pulso_incident_resolve`, `pulso_hilo*→pulso_thread*`, `pulso_scopes→pulso_areas`, `pulso_mover_scope→pulso_move_area`; arg `scope_name→area_name`; assertions Spanish→English (`"desconocida"→"unknown"`, `"origen"` still valid as column name — verify against actual error text). Update `test_tools_list` expected names. Update `test_origin_permitido_ok` origin to a still-allowed value if the allowlist changed (check `mcp/server.py` origin check).
- [ ] **Step 4: Run full suite** locally (reset schema first): `DROP SCHEMA public CASCADE; CREATE SCHEMA public;` then `pytest tests/ -q`. Iterate until green.
- [ ] **Step 5: Add coverage gate** — `ci.yml` test step: `pytest tests/ -v --cov=app --cov-fail-under=90 --cov-report=term-missing`. Run locally; if under 90%, add focused tests for the lowest-covered new modules (accounts/service, projects/access, accounts/router, members).
- [ ] **Step 6: ruff + mypy** clean.
- [ ] **Step 7: Commit** `git commit -m "test: modernize MCP/auth suite for multi-account + isolation/grant coverage + 90% gate"`

---

### Task 13: PR + deploy

- [ ] **Step 1:** Push branch `git push -u origin feat/multi-account`.
- [ ] **Step 2:** Watch CI green (ruff, mypy, migration round-trip, pytest+coverage≥90%).
- [ ] **Step 3:** Open PR with `gh pr create` summarizing the design + linking the spec.
- [ ] **Step 4:** After merge to main, tag `v2026.MM.DD-N` → deploy.yml builds + deploys + `alembic upgrade head` runs v0012 on the server (pre-migration `pg_dump` first, per runbook).
- [ ] **Step 5:** Verify on server: existing data folded into the default account; you are owner+superadmin; existing MCP tokens still work.

## Self-review notes

- Spec coverage: accounts (T1,2,11) · users/projects columns (T1,11) · project_members matrix (T1,8) · access chokepoint (T3) · super-admin/account creation (T2,6,7) · member matrix + token UX (T8,9) · token scope≤role (T9) · migration (T11) · MCP impact (T9, mostly unchanged) · test modernization + 90% (T12) · PR/deploy (T13). All covered.
- Type consistency: `create_account(...)->(Account,User)`, `accessible_project_ids->set[UUID]`, `user_role_on_project->str|None`, `require_project_access(...,need_write=)->None`, `create_project(...,account_id=)` used consistently across tasks.
- Ordering caveat: Task 3's test depends on Task 4's `create_project(account_id=)`; run T3 tests at the end of T4 (noted in T3 Step 2).
