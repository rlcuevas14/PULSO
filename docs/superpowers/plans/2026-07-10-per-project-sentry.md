# Per-project Sentry Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each Pulso project optionally receives its Sentry issues (correctly `project_id`-stamped) via one account-level Sentry connection routed by project slug; optional outbound resolve/backfill.

**Architecture:** New account-level `sentry_connections` table (webhook token + secrets). New tokened route `POST /webhooks/sentry/{token}` → token→account, optional HMAC, dual-shape payload parse, slug→project routing scoped to the account. Outbound fns take explicit `(api_token, org_slug, base_url)` resolved from the issue's account connection. Owner page `/account/integrations` with in-app guides; per-project field `sentry_project_slug`.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic (raw-SQL migrations), Jinja2+HTMX, pytest-asyncio, i18n JSON catalogs (en/es/fr).

**Spec:** `docs/superpowers/specs/2026-07-10-per-project-sentry-design.md`

## Global Constraints

- i18n: NEVER hardcode user-visible strings; add every key to **all three** catalogs (`app/i18n/locales/{en,es,fr}.json`) or `tests/test_i18n.py` fails CI.
- Every mutation on items emits `ItemEvent`; webhook ingest keeps existing audit behavior.
- Migration head advances `v0016 → v0017`. Local test DB may be dirty: reset `pulso_test` with `DROP SCHEMA public CASCADE; CREATE SCHEMA public;` before the first run.
- Tests: `TEST_DATABASE_URL=... DEBUG=true SECRET_KEY=any-test-secret python -m pytest tests/ -q` + `ruff check app/ tests/` + `python -m mypy app/`. CI (pgvector/pg16) is the gate; ≥90% coverage.
- Design system: only `.p-*` classes + semantic tokens in templates; no gray/blue palette classes; no opacity modifiers on semantic tokens.
- Webhook ack < 1s: no outbound calls in the inbound request path.
- Legacy `POST /webhooks/sentry` (global env secret) stays functional, deprecated.
- Secrets on `/account/integrations` display plaintext to the owner — same pattern as `projects_settings.html` github secret (spec §8 amended accordingly).

---

### Task 1: Migration v0017 + ORM models

**Files:**
- Create: `migrations/versions/v0017_sentry_connections.py`
- Modify: `app/webhooks/models.py` (add `SentryConnection`, add `SentryIssue.account_id`)
- Modify: `app/projects/models.py:34-38` (drop 3 dead cols, add `sentry_project_slug` + unique)

**Interfaces:**
- Produces: `SentryConnection(id, account_id, webhook_token, client_secret, api_token, org_slug, base_url, created_at, updated_at)`; `SentryIssue.account_id: uuid|None`; `Project.sentry_project_slug: str|None`.

- [ ] **Step 1: migration**

```python
"""v0017: account-level Sentry connections + per-project slug routing.

sentry_connections (1:1 account) holds the webhook token + secrets. projects gain
sentry_project_slug (unique per account) and DROP the three vestigial per-project
sentry columns (written by the settings form since v0010 but never read by runtime
code). sentry_issues gain account_id so unmatched rows are tenancy-safe.
"""

from alembic import op

revision = "v0017"
down_revision = "v0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE sentry_connections (
            id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            account_id     uuid NOT NULL UNIQUE REFERENCES accounts(id) ON DELETE CASCADE,
            webhook_token  TEXT NOT NULL UNIQUE,
            client_secret  TEXT,
            api_token      TEXT,
            org_slug       TEXT,
            base_url       TEXT,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("ALTER TABLE projects ADD COLUMN sentry_project_slug TEXT")
    op.execute("""
        ALTER TABLE projects ADD CONSTRAINT projects_account_sentry_slug_uniq
        UNIQUE (account_id, sentry_project_slug)
    """)
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS sentry_client_secret")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS sentry_api_token")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS sentry_org")
    op.execute("""
        ALTER TABLE sentry_issues ADD COLUMN account_id uuid
        REFERENCES accounts(id) ON DELETE CASCADE
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE sentry_issues DROP COLUMN account_id")
    op.execute("ALTER TABLE projects DROP CONSTRAINT projects_account_sentry_slug_uniq")
    op.execute("ALTER TABLE projects DROP COLUMN sentry_project_slug")
    op.execute("ALTER TABLE projects ADD COLUMN sentry_client_secret TEXT")
    op.execute("ALTER TABLE projects ADD COLUMN sentry_api_token TEXT")
    op.execute("ALTER TABLE projects ADD COLUMN sentry_org TEXT")
    op.execute("DROP TABLE sentry_connections")
```

- [ ] **Step 2: ORM — `app/webhooks/models.py`**: add to `SentryIssue` (after `project_id`):

```python
    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True
    )
```

and append the new model (module end):

```python
class SentryConnection(Base):
    """Account-level Sentry connection (1:1). The webhook token routes + authenticates
    inbound events; secrets enable HMAC verify (client_secret) and outbound (api_token)."""

    __tablename__ = "sentry_connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"),
        unique=True, nullable=False,
    )
    webhook_token: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    client_secret: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    api_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    org_slug: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    base_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

(add `Text` to the sqlalchemy import.)

- [ ] **Step 3: `app/projects/models.py`** — replace lines 34-38 (the 3 secret columns) with:

```python
    # Sentry routing: this project's slug in the account's Sentry org (spec 2026-07-10)
    sentry_project_slug: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
```

and add to `__table_args__`:

```python
    __table_args__ = (
        UniqueConstraint("account_id", "slug", name="projects_account_slug_uniq"),
        UniqueConstraint("account_id", "sentry_project_slug", name="projects_account_sentry_slug_uniq"),
    )
```

- [ ] **Step 4: smoke** — `pytest tests/test_webhooks_service.py -q` passes (create_all picks up new schema; reset dirty DB first). Note: `projects_settings.html` still references dropped attrs — Jinja `Undefined or ''` renders empty, no crash (fixed properly in Task 5).
- [ ] **Step 5: Commit** `feat(sentry): v0017 schema — account connections, project slug routing, issue account stamp`

---

### Task 2: Connection service (`app/webhooks/connection.py`)

**Files:**
- Create: `app/webhooks/connection.py`
- Test: `tests/test_sentry_connection.py`

**Interfaces (produced — later tasks consume exactly these):**
- `DEFAULT_BASE_URL = "https://sentry.io"`
- `class SentryConfigError(ValueError)`
- `async get_by_token(db, token: str) -> SentryConnection | None`
- `async get_for_account(db, account_id) -> SentryConnection | None`
- `async get_or_create(db, account_id) -> SentryConnection` (generates `webhook_token = secrets.token_urlsafe(32)`)
- `async update_connection(db, conn, *, client_secret, api_token, org_slug, base_url) -> SentryConnection` (empty→None; validates base_url `^https?://host[:port]$` else `SentryConfigError`)
- `async regenerate_token(db, conn) -> SentryConnection`
- `def effective_base_url(conn) -> str`
- `async outbound(db, account_id) -> SentryConnection | None` (only if `api_token` set)
- `async route_project(db, account_id, slug: str | None) -> Project | None` — slug match within account; slug None → the single slug-mapped project of the account if exactly one, else None
- `async count_unmatched(db, account_id) -> int` — `project_id IS NULL AND (account_id = X OR account_id IS NULL)`
- `async reattach_unmatched(db, account_id) -> int` — match those rows' text `project` slug to the account's `sentry_project_slug` mappings; stamp `project_id` + `account_id`; returns count

- [ ] **Step 1: failing tests** (`tests/test_sentry_connection.py`) — use existing account/project fixtures pattern from `tests/` (create Account + Projects directly like other tests do):

```python
"""Tenancy-safe Sentry connection service."""
import pytest

from app.accounts.models import Account
from app.projects.models import Project
from app.webhooks import connection as sc
from app.webhooks.models import SentryIssue


async def _account(db, name="acme"):
    import uuid as _u
    a = Account(name=name, slug=f"{name}-{_u.uuid4().hex[:6]}")
    db.add(a)
    await db.flush()
    return a


async def _project(db, account, slug, sentry_slug=None):
    p = Project(name=slug, slug=slug, account_id=account.id, sentry_project_slug=sentry_slug)
    db.add(p)
    await db.flush()
    return p


@pytest.mark.asyncio
async def test_get_or_create_generates_token_once(db):
    a = await _account(db)
    c1 = await sc.get_or_create(db, a.id)
    c2 = await sc.get_or_create(db, a.id)
    assert c1.id == c2.id and len(c1.webhook_token) >= 32
    assert await sc.get_by_token(db, c1.webhook_token) is not None
    assert await sc.get_by_token(db, "nope") is None


@pytest.mark.asyncio
async def test_update_validates_base_url_and_blanks(db):
    a = await _account(db)
    c = await sc.get_or_create(db, a.id)
    await sc.update_connection(db, c, client_secret=" s ", api_token="",
                               org_slug="org", base_url="https://sentry.example.com:9000")
    assert c.client_secret == "s" and c.api_token is None
    assert sc.effective_base_url(c) == "https://sentry.example.com:9000"
    with pytest.raises(sc.SentryConfigError):
        await sc.update_connection(db, c, client_secret="", api_token="",
                                   org_slug="", base_url="https://host/path")
    await sc.update_connection(db, c, client_secret="", api_token="", org_slug="", base_url="")
    assert sc.effective_base_url(c) == sc.DEFAULT_BASE_URL


@pytest.mark.asyncio
async def test_regenerate_rotates(db):
    a = await _account(db)
    c = await sc.get_or_create(db, a.id)
    old = c.webhook_token
    await sc.regenerate_token(db, c)
    assert c.webhook_token != old
    assert await sc.get_by_token(db, old) is None


@pytest.mark.asyncio
async def test_route_project_scoped_to_account(db):
    a1, a2 = await _account(db, "a1"), await _account(db, "a2")
    p1 = await _project(db, a1, "p1", sentry_slug="web")
    await _project(db, a2, "p2", sentry_slug="web")  # same sentry slug, other account
    hit = await sc.route_project(db, a1.id, "web")
    assert hit is not None and hit.id == p1.id
    assert await sc.route_project(db, a1.id, "unknown") is None
    # slug None → single mapped project fallback
    assert (await sc.route_project(db, a1.id, None)).id == p1.id
    await _project(db, a1, "p3", sentry_slug="api")
    assert await sc.route_project(db, a1.id, None) is None  # ambiguous now


@pytest.mark.asyncio
async def test_reattach_unmatched_is_tenancy_safe(db):
    a1, a2 = await _account(db, "a1"), await _account(db, "a2")
    p1 = await _project(db, a1, "p1", sentry_slug="web")
    db.add(SentryIssue(sentry_issue_id="U1", project="web", title="t", account_id=a1.id))
    db.add(SentryIssue(sentry_issue_id="U2", project="web", title="t", account_id=a2.id))
    db.add(SentryIssue(sentry_issue_id="U3", project="web", title="t"))  # legacy NULL account
    await db.flush()
    assert await sc.count_unmatched(db, a1.id) == 2  # own + legacy, NOT a2's
    n = await sc.reattach_unmatched(db, a1.id)
    assert n == 2
    from sqlalchemy import select
    rows = (await db.execute(select(SentryIssue))).scalars().all()
    by_id = {r.sentry_issue_id: r for r in rows}
    assert by_id["U1"].project_id == p1.id and by_id["U3"].project_id == p1.id
    assert by_id["U2"].project_id is None  # a2's row untouched
```

- [ ] **Step 2: run, expect FAIL** (`ModuleNotFoundError: app.webhooks.connection`).
- [ ] **Step 3: implement** `app/webhooks/connection.py`:

```python
"""Account-level Sentry connection: token routing, config, unmatched re-attach.

Tenancy chokepoint for webhooks (spec 2026-07-10 §4.2): every lookup here is
scoped by account_id, so a webhook token can only ever write inside its account.
"""

import re
import secrets
import uuid

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.projects.models import Project
from app.webhooks.models import SentryConnection, SentryIssue

DEFAULT_BASE_URL = "https://sentry.io"
# scheme://host[:port] only — no path/query (mirrors Sentry's system.url-prefix rule)
_BASE_URL_RE = re.compile(r"^https?://[A-Za-z0-9.-]+(:\d+)?$")


class SentryConfigError(ValueError):
    pass


async def get_by_token(db: AsyncSession, token: str) -> SentryConnection | None:
    return (await db.execute(
        select(SentryConnection).where(SentryConnection.webhook_token == token)
    )).scalar_one_or_none()


async def get_for_account(db: AsyncSession, account_id: uuid.UUID) -> SentryConnection | None:
    return (await db.execute(
        select(SentryConnection).where(SentryConnection.account_id == account_id)
    )).scalar_one_or_none()


async def get_or_create(db: AsyncSession, account_id: uuid.UUID) -> SentryConnection:
    conn = await get_for_account(db, account_id)
    if conn is None:
        conn = SentryConnection(account_id=account_id, webhook_token=secrets.token_urlsafe(32))
        db.add(conn)
        await db.flush()
    return conn


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


async def update_connection(
    db: AsyncSession, conn: SentryConnection, *,
    client_secret: str | None, api_token: str | None,
    org_slug: str | None, base_url: str | None,
) -> SentryConnection:
    base = _clean(base_url)
    if base and not _BASE_URL_RE.match(base):
        raise SentryConfigError("base_url must be http(s)://host[:port] with no path.")
    conn.client_secret = _clean(client_secret)
    conn.api_token = _clean(api_token)
    conn.org_slug = _clean(org_slug)
    conn.base_url = base
    await db.flush()
    return conn


async def regenerate_token(db: AsyncSession, conn: SentryConnection) -> SentryConnection:
    conn.webhook_token = secrets.token_urlsafe(32)
    await db.flush()
    return conn


def effective_base_url(conn: SentryConnection | None) -> str:
    return (conn.base_url if conn and conn.base_url else DEFAULT_BASE_URL)


async def outbound(db: AsyncSession, account_id: uuid.UUID | None) -> SentryConnection | None:
    """Connection usable for outbound API calls (feature B), or None."""
    if account_id is None:
        return None
    conn = await get_for_account(db, account_id)
    return conn if (conn and conn.api_token) else None


async def route_project(
    db: AsyncSession, account_id: uuid.UUID, slug: str | None
) -> Project | None:
    """Slug → project, scoped to the account. slug=None routes only when the account
    has exactly one slug-mapped project (event_alert payloads carry no slug)."""
    if slug:
        return (await db.execute(
            select(Project).where(
                Project.account_id == account_id, Project.sentry_project_slug == slug
            )
        )).scalar_one_or_none()
    mapped = (await db.execute(
        select(Project).where(
            Project.account_id == account_id, Project.sentry_project_slug.is_not(None)
        ).limit(2)
    )).scalars().all()
    return mapped[0] if len(mapped) == 1 else None


def _unmatched_filter(account_id: uuid.UUID):
    return (
        SentryIssue.project_id.is_(None),
        or_(SentryIssue.account_id == account_id, SentryIssue.account_id.is_(None)),
    )


async def count_unmatched(db: AsyncSession, account_id: uuid.UUID) -> int:
    from sqlalchemy import func
    return int(await db.scalar(
        select(func.count()).select_from(SentryIssue).where(*_unmatched_filter(account_id))
    ) or 0)


async def reattach_unmatched(db: AsyncSession, account_id: uuid.UUID) -> int:
    """Attach NULL-project rows to this account's projects by their text slug.
    NULL-account rows are claimable (legacy single-account era); other accounts' are not."""
    mapped = (await db.execute(
        select(Project.id, Project.sentry_project_slug).where(
            Project.account_id == account_id, Project.sentry_project_slug.is_not(None)
        )
    )).all()
    total = 0
    for pid, slug in mapped:
        res = await db.execute(
            update(SentryIssue)
            .where(*_unmatched_filter(account_id), SentryIssue.project == slug)
            .values(project_id=pid, account_id=account_id)
        )
        total += res.rowcount or 0
    await db.flush()
    return total
```

- [ ] **Step 4: run, expect PASS** — `pytest tests/test_sentry_connection.py -q`
- [ ] **Step 5: Commit** `feat(sentry): account-level connection service (token, config, routing, re-attach)`

---

### Task 3: Dual-shape payload parser + ingest stamping

**Files:**
- Modify: `app/webhooks/service.py:58-109` (`ingest_sentry`)
- Test: extend `tests/test_webhooks_service.py`

**Interfaces:**
- Produces: `parse_sentry_payload(payload: dict) -> dict` with keys `{sentry_id, title, level, slug, web_url, count, first_seen, last_seen}` (raises `ValueError` if no id); `ingest_sentry(db, payload, *, account_id=None, project_id=None)`.
- Backward compatible: existing callers/tests (no kwargs) keep working; NULL stamps.

- [ ] **Step 1: failing tests** (append to `tests/test_webhooks_service.py`):

```python
def test_parse_sentry_payload_shapes():
    # issue webhook (primary): slug from data.issue.project.slug
    p1 = ws.parse_sentry_payload({"data": {"issue": {
        "id": 42, "title": "Boom", "level": "warning",
        "project": {"slug": "web", "id": 7}, "permalink": "https://s/x"}}})
    assert p1["sentry_id"] == "42" and p1["slug"] == "web" and p1["level"] == "warning"
    # event_alert webhook: no slug, web_url
    p2 = ws.parse_sentry_payload({"data": {"event": {
        "issue_id": "77", "title": "Alert", "level": "error",
        "project": 7, "web_url": "https://s/y"}}})
    assert p2["sentry_id"] == "77" and p2["slug"] is None and p2["web_url"] == "https://s/y"
    # legacy plugin: flat
    p3 = ws.parse_sentry_payload({"id": "9", "project": "api", "level": "info", "url": "u",
                                  "message": "m"})
    assert p3["sentry_id"] == "9" and p3["slug"] == "api"
    with pytest.raises(ValueError):
        ws.parse_sentry_payload({"data": {"issue": {"title": "no id"}}})


@pytest.mark.asyncio
async def test_ingest_stamps_account_and_project(db):
    from sqlalchemy import select

    from app.accounts.models import Account
    from app.projects.models import Project
    from app.webhooks.models import SentryIssue
    a = Account(name="x", slug=f"x-{uuid.uuid4().hex[:6]}")
    db.add(a); await db.flush()
    p = Project(name="w", slug="w", account_id=a.id, sentry_project_slug="web")
    db.add(p); await db.flush()
    payload = {"data": {"issue": {"id": "ST1", "title": "T", "project": {"slug": "web"}}}}
    await ws.ingest_sentry(db, payload, account_id=a.id, project_id=p.id)
    row = (await db.execute(select(SentryIssue).where(
        SentryIssue.sentry_issue_id == "ST1"))).scalar_one()
    assert row.account_id == a.id and row.project_id == p.id
    # dedup path heals a NULL-project row once routing is known
    db.add(SentryIssue(sentry_issue_id="ST2", project="web", title="t"))
    await db.flush()
    await ws.ingest_sentry(db, {"data": {"issue": {"id": "ST2", "title": "t",
                            "project": {"slug": "web"}}}}, account_id=a.id, project_id=p.id)
    row2 = (await db.execute(select(SentryIssue).where(
        SentryIssue.sentry_issue_id == "ST2"))).scalar_one()
    assert row2.project_id == p.id and row2.account_id == a.id
```

- [ ] **Step 2: run, expect FAIL** (`parse_sentry_payload` undefined; kwargs TypeError).
- [ ] **Step 3: implement** — in `service.py`, extract the parse (replacing lines 65-77 in-place logic) and stamp:

```python
def parse_sentry_payload(payload: dict) -> dict[str, Any]:
    """Normalize the three inbound shapes (issue webhook / event_alert / legacy plugin)
    into one dict. Raises ValueError when no issue id is present (spec §4.3)."""
    data = payload.get("data") or {}
    issue = data.get("issue") or payload.get("issue")
    event = data.get("event")
    if issue:                       # Internal Integration, resource=issue
        src, sentry_id = issue, issue.get("id")
        proj = issue.get("project")
        slug = proj.get("slug") if isinstance(proj, dict) else (str(proj) if proj else None)
        web_url = issue.get("web_url") or issue.get("permalink")
    elif event:                     # Internal Integration, resource=event_alert
        src, sentry_id = event, event.get("issue_id") or event.get("issue.id")
        slug = None                 # alert payloads carry only a numeric project id
        web_url = event.get("web_url")
    else:                           # legacy per-project plugin (flat, unsigned)
        src, sentry_id = payload, payload.get("id")
        proj = payload.get("project")
        slug = str(proj) if proj else None
        web_url = payload.get("url")
    if not sentry_id:
        raise ValueError("Falta el id del issue de Sentry")
    title = _sanitize(src.get("title") or src.get("culprit") or src.get("message")
                      or "Sentry issue", 500)
    level = src.get("level", "error")
    if level not in ("error", "warning", "info"):
        level = "error"
    try:
        count = int(str(src.get("count") or 1))
    except (TypeError, ValueError):
        count = 1
    return {"sentry_id": str(sentry_id), "title": title, "level": level,
            "slug": (slug or "")[:60] or None, "web_url": web_url, "count": count,
            "first_seen": _parse_dt(src.get("firstSeen")),
            "last_seen": _parse_dt(src.get("lastSeen"))}
```

Rewrite `ingest_sentry` to use it (keep docstring/policy comment), signature
`async def ingest_sentry(db, payload, *, account_id=None, project_id=None)`:
- create path: `SentryIssue(..., project=parsed["slug"] or "desconocido", account_id=account_id, project_id=project_id, ...)`; keep AgentRun enqueue with `project_id=issue.project_id`.
- dedup path: increment/update as today **plus** heal: `if issue.project_id is None and project_id is not None: issue.project_id = project_id`; same for `account_id`.

- [ ] **Step 4: run, expect PASS** — `pytest tests/test_webhooks_service.py -q` (old ingest tests still green).
- [ ] **Step 5: Commit** `feat(sentry): dual-shape payload parser + account/project stamping in ingest`

---

### Task 4: Tokened webhook route

**Files:**
- Modify: `app/webhooks/router.py` (new route; legacy stays)
- Test: `tests/test_sentry_webhook_routing.py`

**Interfaces:**
- Consumes: `connection.get_by_token/route_project`, `service.parse_sentry_payload/ingest_sentry/verify_sentry_signature`.
- Produces: `POST /webhooks/sentry/{token}` — 404 unknown token; 401 bad HMAC (signed mode); 200 otherwise (incl. unmatched).

- [ ] **Step 1: failing tests** (`tests/test_sentry_webhook_routing.py`):

```python
"""Tokened per-account Sentry webhook: auth modes + slug routing + isolation."""
import hashlib
import hmac
import json
import uuid

import pytest
from sqlalchemy import select

from app.accounts.models import Account
from app.projects.models import Project
from app.webhooks import connection as sc
from app.webhooks.models import SentryIssue


async def _setup(db, sentry_slug="web", secret=None):
    a = Account(name="acc", slug=f"acc-{uuid.uuid4().hex[:6]}")
    db.add(a); await db.flush()
    p = Project(name="P", slug=f"p-{uuid.uuid4().hex[:6]}", account_id=a.id,
                sentry_project_slug=sentry_slug)
    db.add(p); await db.flush()
    conn = await sc.get_or_create(db, a.id)
    if secret:
        conn.client_secret = secret
    await db.commit()
    return a, p, conn


def _payload(sid, slug="web"):
    return json.dumps({"data": {"issue": {"id": sid, "title": "Boom",
                                          "project": {"slug": slug}}}}).encode()


@pytest.mark.asyncio
async def test_unknown_token_404(client):
    r = await client.post("/webhooks/sentry/not-a-token", content=b"{}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_unsigned_mode_routes_by_slug(client, db):
    a, p, conn = await _setup(db)
    r = await client.post(f"/webhooks/sentry/{conn.webhook_token}", content=_payload("R1"))
    assert r.status_code == 200 and r.json()["created"] is True
    row = (await db.execute(select(SentryIssue).where(
        SentryIssue.sentry_issue_id == "R1"))).scalar_one()
    assert row.project_id == p.id and row.account_id == a.id


@pytest.mark.asyncio
async def test_signed_mode_verifies_hmac(client, db):
    _, _, conn = await _setup(db, secret="topsecret")
    body = _payload("R2")
    sig = hmac.new(b"topsecret", body, hashlib.sha256).hexdigest()
    ok = await client.post(f"/webhooks/sentry/{conn.webhook_token}", content=body,
                           headers={"sentry-hook-signature": sig})
    assert ok.status_code == 200
    bad = await client.post(f"/webhooks/sentry/{conn.webhook_token}", content=body,
                            headers={"sentry-hook-signature": "forged"})
    assert bad.status_code == 401
    missing = await client.post(f"/webhooks/sentry/{conn.webhook_token}", content=body)
    assert missing.status_code == 401


@pytest.mark.asyncio
async def test_unmatched_slug_parks_with_account(client, db):
    a, _, conn = await _setup(db, sentry_slug="other")
    r = await client.post(f"/webhooks/sentry/{conn.webhook_token}", content=_payload("R3"))
    assert r.status_code == 200
    row = (await db.execute(select(SentryIssue).where(
        SentryIssue.sentry_issue_id == "R3"))).scalar_one()
    assert row.project_id is None and row.account_id == a.id


@pytest.mark.asyncio
async def test_cross_account_isolation(client, db):
    a1, p1, conn1 = await _setup(db)                      # account 1 maps "web"
    a2 = Account(name="a2", slug=f"a2-{uuid.uuid4().hex[:6]}")
    db.add(a2); await db.flush()
    p2 = Project(name="P2", slug=f"p2-{uuid.uuid4().hex[:6]}", account_id=a2.id,
                 sentry_project_slug="web")               # same sentry slug, account 2
    db.add(p2); await db.commit()
    r = await client.post(f"/webhooks/sentry/{conn1.webhook_token}", content=_payload("R4"))
    assert r.status_code == 200
    row = (await db.execute(select(SentryIssue).where(
        SentryIssue.sentry_issue_id == "R4"))).scalar_one()
    assert row.project_id == p1.id      # token's account wins — never p2
    assert row.project_id != p2.id


@pytest.mark.asyncio
async def test_bad_json_and_missing_id_422(client, db):
    _, _, conn = await _setup(db)
    bad = await client.post(f"/webhooks/sentry/{conn.webhook_token}", content=b"not json")
    assert bad.status_code == 422
    noid = await client.post(f"/webhooks/sentry/{conn.webhook_token}",
                             content=json.dumps({"data": {"issue": {"title": "x"}}}).encode())
    assert noid.status_code == 422
```

- [ ] **Step 2: run, expect FAIL** (404 for every call — route missing... the first test passes trivially; others fail).
- [ ] **Step 3: implement** — add to `app/webhooks/router.py` (before the legacy route to avoid path shadowing issues; FastAPI matches `/sentry/{token}` vs `/sentry` fine either way):

```python
@router.post("/sentry/{token}")
async def sentry_webhook_tokened(
    token: str, request: Request, db: AsyncSession = Depends(get_db)
) -> Response:
    """Per-account inbound webhook (spec 2026-07-10). Token routes to the account;
    HMAC verify only when the account stored a client_secret (signed mode).
    Always fast-ack: no outbound calls here (Sentry disables webhooks that time out)."""
    conn = await connection.get_by_token(db, token)
    if conn is None:
        return JSONResponse({"error": "unknown webhook token"}, status_code=404)
    body = await request.body()
    if conn.client_secret:
        sig = request.headers.get("sentry-hook-signature")
        if not service.verify_sentry_signature(conn.client_secret, body, sig):
            return JSONResponse({"error": "invalid signature"}, status_code=401)
    try:
        payload = json.loads(body)
        parsed = service.parse_sentry_payload(payload)
        project = await connection.route_project(db, conn.account_id, parsed["slug"])
        result = await service.ingest_sentry(
            db, payload, account_id=conn.account_id,
            project_id=project.id if project else None,
        )
        await db.commit()
    except (ValueError, json.JSONDecodeError) as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    if project is None:
        logger.warning("sentry webhook: unmatched slug %r for account %s",
                       parsed["slug"], conn.account_id)
    return JSONResponse(result)
```

(imports: `from app.webhooks import connection`, `import logging; logger = logging.getLogger("pulso.webhooks")`.)

- [ ] **Step 4: run, expect PASS** — `pytest tests/test_sentry_webhook_routing.py tests/test_webhooks_service.py -q`
- [ ] **Step 5: Commit** `feat(sentry): tokened per-account webhook route with slug routing + optional HMAC`

---

### Task 5: Project settings — slug field replaces dead secrets

**Files:**
- Modify: `app/projects/router.py:103-135` (form params), `app/templates/projects_settings.html:106-130`, `app/i18n/locales/{en,es,fr}.json`
- Test: extend whichever test posts `/projects/{slug}/settings` (grep `projects_settings` in tests; update), plus new uniqueness test.

**Interfaces:**
- Produces: form field `sentry_project_slug`; friendly 422 on duplicate within account.

- [ ] **Step 1**: router — replace the three `sentry_*` Form params with `sentry_project_slug: str = Form("")`; in the update dict replace the three entries with `"sentry_project_slug": sentry_project_slug.strip() or None`. Pre-check duplicates:

```python
    new_slug = sentry_project_slug.strip() or None
    if new_slug:
        clash = await db.scalar(select(Project.id).where(
            Project.account_id == user.account_id,
            Project.sentry_project_slug == new_slug, Project.id != project.id))
        if clash:
            return Response(status_code=422,
                            content=_t("projects.sentry_slug_taken", resolve_lang(request)))
```

(import `select` from sqlalchemy; `Project` via `ps` module's models import or direct.)

- [ ] **Step 2**: template — replace the two sentry secret inputs + org input inside the `webhook_secrets` details block with:

```html
          <div>
            <label class="p-label">{{ t("projects.sentry_slug") }}</label>
            <input name="sentry_project_slug" type="text" value="{{ project.sentry_project_slug or '' }}"
              class="w-full p-input text-xs font-mono" placeholder="my-sentry-project">
            <p class="text-xs text-muted mt-1">{{ t("projects.sentry_slug_help") }}</p>
          </div>
```

(github secret input stays.)

- [ ] **Step 3**: i18n ×3 — remove `projects.sentry_api_token`, `projects.sentry_client_secret`, `projects.sentry_org`; add:
  - en: `"projects.sentry_slug": "Sentry project slug"`, `"projects.sentry_slug_help": "The project slug in your account's Sentry org — incoming Sentry issues with this slug land in this project. Configure the org connection at Account → Integrations."`, `"projects.sentry_slug_taken": "Another project in this account already uses that Sentry slug."`
  - es: `"projects.sentry_slug": "Slug del proyecto en Sentry"`, `"projects.sentry_slug_help": "El slug del proyecto en la organización de Sentry de tu cuenta — los issues entrantes con este slug aterrizan en este proyecto. Configura la conexión en Cuenta → Integraciones."`, `"projects.sentry_slug_taken": "Otro proyecto de esta cuenta ya usa ese slug de Sentry."`
  - fr: `"projects.sentry_slug": "Slug du projet Sentry"`, `"projects.sentry_slug_help": "Le slug du projet dans l'organisation Sentry de votre compte — les issues entrantes avec ce slug arrivent dans ce projet. Configurez la connexion dans Compte → Intégrations."`, `"projects.sentry_slug_taken": "Un autre projet de ce compte utilise déjà ce slug Sentry."`
- [ ] **Step 4**: fix/extend tests referencing the old fields; add duplicate-slug 422 test. Run `pytest tests/ -q -k "settings or i18n"`.
- [ ] **Step 5: Commit** `feat(sentry): per-project sentry slug in settings (replaces dead per-project secrets)`

---

### Task 6: Outbound refactor (resolve / detail / backfill fetch)

**Files:**
- Modify: `app/webhooks/service.py:136-176 (resolve_issue), 221-233 (fetch_sentry_issues), 288-311 (fetch_issue_detail), 314-326 (resolve_in_sentry)`
- Modify: `app/mcp/tools.py:553-575 (pulso_incident)`
- Test: update `tests/test_webhooks_service.py:148-182`, `tests/test_mcp.py:308` area.

**Interfaces:**
- Produces:
  - `async fetch_sentry_issues(token, org, project, query="is:unresolved", base_url=DEFAULT_BASE_URL) -> list`
  - `async fetch_issue_detail(issue_id, *, api_token=None, base_url=None) -> dict` (falls back to `settings.sentry_api_token` + sentry.io when kwargs None — legacy env mode)
  - `async resolve_in_sentry(issue_id, *, api_token=None, org_slug=None, base_url=None) -> bool` (same fallback; org-scoped endpoint when org_slug present, else legacy `/api/0/issues/{id}/`; one retry on 429 honoring `Retry-After` capped 5s)
  - `resolve_issue(...)` internally resolves the issue's account connection via `connection.outbound(db, issue.account_id)` and passes explicit params; falls back to env settings when no connection.

- [ ] **Step 1**: adapt the two existing outbound tests to keyword form and add a 429 test:

```python
# in test_sentry_api_calls_mocked: replace the two calls
    detail = await ws.fetch_issue_detail("123", api_token="tok")
    assert detail["title"] == "T"
    assert await ws.resolve_in_sentry("123", api_token="tok", org_slug="org") is True
    assert isinstance(await ws.fetch_sentry_issues("tok", "org", "proj"), list)


@pytest.mark.asyncio
async def test_resolve_in_sentry_429_retries(monkeypatch):
    import httpx
    calls = []

    class _R429:
        status_code = 429
        headers = {"Retry-After": "0"}

    class _ROK:
        status_code = 200
        headers = {}

    async def fake_put(self, url, **kw):
        calls.append(url)
        return _R429() if len(calls) == 1 else _ROK()

    monkeypatch.setattr(httpx.AsyncClient, "put", fake_put)
    assert await ws.resolve_in_sentry("9", api_token="t", org_slug="o",
                                      base_url="https://sh.example.com") is True
    assert len(calls) == 2 and calls[0].startswith("https://sh.example.com/api/0/organizations/o/")
```

- [ ] **Step 2: run, expect FAIL** (TypeError unexpected kwarg).
- [ ] **Step 3: implement**:

```python
async def resolve_in_sentry(
    issue_id: str, *, api_token: str | None = None,
    org_slug: str | None = None, base_url: str | None = None,
) -> bool:
    """Marca el issue como resuelto en Sentry (scope Issue&Event: Write).
    Sin api_token explícito cae al modo legacy por env (deprecated)."""
    token = api_token or settings.sentry_api_token
    if not token:
        return False
    base = base_url or connection.DEFAULT_BASE_URL
    org = org_slug or settings.sentry_org
    url = (f"{base}/api/0/organizations/{org}/issues/{issue_id}/" if org
           else f"{base}/api/0/issues/{issue_id}/")     # legacy fallback sin org
    headers = {"Authorization": f"Bearer {token}", "content-type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.put(url, headers=headers, json={"status": "resolved"})
        if r.status_code == 429:                         # honor Retry-After, one retry
            try:
                delay = min(float(r.headers.get("Retry-After", "1")), 5.0)
            except ValueError:
                delay = 1.0
            await asyncio.sleep(delay)
            r = await client.put(url, headers=headers, json={"status": "resolved"})
    return r.status_code < 300
```

`fetch_issue_detail`: add kwargs, `token = api_token or settings.sentry_api_token`, RuntimeError unchanged when neither, `base = base_url or DEFAULT`; both GET urls use `{base}`. `fetch_sentry_issues`: add `base_url=connection.DEFAULT_BASE_URL` param, url uses it. `resolve_issue`: before the `if in_sentry:` call:

```python
    conn = await connection.outbound(db, issue.account_id)
    ...
            sentry_done = await resolve_in_sentry(
                issue.sentry_issue_id,
                api_token=conn.api_token if conn else None,
                org_slug=conn.org_slug if conn else None,
                base_url=connection.effective_base_url(conn) if conn else None,
            )
```

`mcp/tools.py pulso_incident` (line 567): resolve params first:

```python
    from app.webhooks import connection as sconn
    conn = await sconn.outbound(db, issue.account_id)
    try:
        detail = await wservice.fetch_issue_detail(
            issue.sentry_issue_id,
            api_token=conn.api_token if conn else None,
            base_url=sconn.effective_base_url(conn) if conn else None,
        )
```

(imports in service.py: `import asyncio`, `from app.webhooks import connection`.)

- [ ] **Step 4: run, expect PASS** — `pytest tests/test_webhooks_service.py tests/test_mcp.py -q`
- [ ] **Step 5: Commit** `feat(sentry): outbound calls use account connection (org endpoint, base_url, 429 retry)`

---

### Task 7: Backfill via stored connection

**Files:**
- Modify: `app/ui/router.py:1005-1033`, `app/webhooks/service.py:236-255 (backfill_issues)`, `app/templates/incidentes.html:28-55`, i18n ×3.

**Interfaces:**
- Produces: `backfill_issues(db, issues, project_slug, *, account_id=None, project_id=None)`; UI form posts only `query`.

- [ ] **Step 1**: `backfill_issues` — add kwargs, pass through to `ingest_sentry(db, {...}, account_id=account_id, project_id=project_id)`.
- [ ] **Step 2**: rewrite `ui_backfill_sentry`: keep owner gate; resolve `pid = await _project_id(db, user, request)`; load project; errors via i18n:

```python
    project = await db.get(Project, pid)
    conn = await sconn.outbound(db, user.account_id)
    lang = resolve_lang(request)
    if conn is None or not conn.org_slug:
        return HTMLResponse(f'<div class="text-sm text-error">'
                            f'{_t("incidents.backfill_not_configured", lang)}</div>')
    if not project or not project.sentry_project_slug:
        return HTMLResponse(f'<div class="text-sm text-error">'
                            f'{_t("incidents.backfill_no_slug", lang)}</div>')
    try:
        issues = await wservice.fetch_sentry_issues(
            conn.api_token, conn.org_slug, project.sentry_project_slug, query,
            base_url=sconn.effective_base_url(conn))
    except Exception as e:
        return HTMLResponse(f'<div class="text-sm text-error">'
                            f'{_t("incidents.backfill_error", lang, error=e)}</div>')
    result = await wservice.backfill_issues(db, issues, project.sentry_project_slug,
                                            account_id=user.account_id, project_id=pid)
```

Form signature drops `org/project/token`, keeps `query: str = Form("is:unresolved")`.
- [ ] **Step 3**: `incidentes.html` modal — delete the `org`, `project`, `token` inputs (keep `query`); change `incidents.backfill_desc` copy to reference the stored connection.
- [ ] **Step 4**: i18n ×3 — remove `incidents.backfill_org_placeholder/_org_aria/_project_placeholder/_project_aria/_token_placeholder/_token_aria`; reword `incidents.backfill_desc`; add `incidents.backfill_not_configured` ("Sentry outbound is not configured. An owner must add the API token and org at Account → Integrations." / es / fr) and `incidents.backfill_no_slug` ("This project has no Sentry slug. Set it in Project → Settings." / es / fr).
- [ ] **Step 5**: fix `tests/test_sprint5.py:283` backfill test (posts form fields — update to new form + seed connection). Run `pytest tests/test_sprint5.py -q`.
- [ ] **Step 6: Commit** `feat(sentry): backfill uses stored account connection + project slug`

---

### Task 8: `/account/integrations` page (guides A & B) + navbar

**Files:**
- Modify: `app/accounts/router.py` (4 endpoints), `app/templates/base.html:69` (menu link)
- Create: `app/templates/account_integrations.html`
- Modify: i18n ×3
- Test: `tests/test_account_integrations.py`

**Interfaces:**
- Produces: `GET /account/integrations` (owner) · `POST /account/integrations` (save config) · `POST /account/integrations/regenerate` · `POST /account/integrations/reattach`.

- [ ] **Step 1: failing tests**:

```python
"""Owner-only Sentry integration page."""
import pytest

# reuse the existing logged-in owner/member client fixtures pattern from
# tests/test_accounts.py (owner_client / member_client or equivalent helpers)


@pytest.mark.asyncio
async def test_integrations_owner_only(owner_client, member_client):
    ok = await owner_client.get("/account/integrations")
    assert ok.status_code == 200 and "/webhooks/sentry/" in ok.text
    denied = await member_client.get("/account/integrations")
    assert denied.status_code in (303, 403)


@pytest.mark.asyncio
async def test_integrations_save_and_regenerate(owner_client):
    r = await owner_client.post("/account/integrations", data={
        "client_secret": "cs", "api_token": "tok", "org_slug": "acme",
        "base_url": "https://sentry.acme.dev"})
    assert r.status_code == 303
    page1 = (await owner_client.get("/account/integrations")).text
    r2 = await owner_client.post("/account/integrations/regenerate")
    assert r2.status_code == 303
    page2 = (await owner_client.get("/account/integrations")).text
    assert page1 != page2  # webhook URL rotated
    bad = await owner_client.post("/account/integrations", data={
        "client_secret": "", "api_token": "", "org_slug": "", "base_url": "https://x/path"})
    assert bad.status_code == 422
```

(If `owner_client`/`member_client` fixtures don't exist under those names, adapt to the codebase's actual auth fixtures found in `tests/test_accounts.py` — same behavior.)

- [ ] **Step 2: run, expect FAIL** (404).
- [ ] **Step 3: implement router** (append to `app/accounts/router.py`):

```python
# ---------- Owner: Sentry integration ----------

@router.get("/account/integrations", response_class=HTMLResponse)
async def account_integrations(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_owner),
):
    conn = await sconn.get_or_create(db, user.account_id)
    await db.commit()
    return templates.TemplateResponse(request, "account_integrations.html", {
        "user": user, "conn": conn,
        "webhook_url": f"{settings.base_url}/webhooks/sentry/{conn.webhook_token}",
        "unmatched": await sconn.count_unmatched(db, user.account_id),
        "reattached": request.session.pop("reattached", None),
    })


@router.post("/account/integrations")
async def account_integrations_save(
    request: Request,
    client_secret: str = Form(""),
    api_token: str = Form(""),
    org_slug: str = Form(""),
    base_url: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_owner),
):
    conn = await sconn.get_or_create(db, user.account_id)
    try:
        await sconn.update_connection(db, conn, client_secret=client_secret,
                                      api_token=api_token, org_slug=org_slug,
                                      base_url=base_url)
    except sconn.SentryConfigError as e:
        return HTMLResponse(str(e), status_code=422)
    await db.commit()
    flash_success(request, message=t("flash.settings_saved", resolve_lang(request)))
    return RedirectResponse("/account/integrations", status_code=303)


@router.post("/account/integrations/regenerate")
async def account_integrations_regenerate(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_owner),
):
    conn = await sconn.get_or_create(db, user.account_id)
    await sconn.regenerate_token(db, conn)
    await db.commit()
    return RedirectResponse("/account/integrations", status_code=303)


@router.post("/account/integrations/reattach")
async def account_integrations_reattach(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_owner),
):
    n = await sconn.reattach_unmatched(db, user.account_id)
    await db.commit()
    request.session["reattached"] = n
    return RedirectResponse("/account/integrations", status_code=303)
```

(imports: `from app.config import settings`, `from app.i18n import t as _t` — match file's existing style, `from app.i18n import resolve_lang`, `from app.ui.flash import flash_success`, `from app.webhooks import connection as sconn`.)

- [ ] **Step 4: template** `account_integrations.html` — extends base, `.p-*` classes only, all copy via `t()`:
  - Card 1 "Inbound (A)": readonly webhook-URL input + copy button (clipboard pattern from `projects_settings.html:28-29`), regenerate button (small, ghost), `client_secret` input (signed mode, optional), collapsible `<details>` **Guide A** = ordered list `sentry.guide_a_step1..5`.
  - Card 2 "Outbound (B)": `api_token`, `org_slug`, `base_url` inputs + collapsible **Guide B** = `sentry.guide_b_step1..3`; note B requires A.
  - Unmatched banner when `unmatched > 0`: `tn("sentry.unmatched", unmatched)` + reattach form (`hx-post` not needed; plain POST + redirect) + `reattached` result line when present.
- [ ] **Step 5: navbar** `base.html:69` — after the members link:

```html
            {% if user.account_role == 'owner' %}<a href="/account/integrations" class="p-menu-item">{{ t("nav.integrations") }}</a>{% endif %}
```

- [ ] **Step 6: i18n ×3** — keys (en shown; es/fr translated equivalents):
  `nav.integrations` "Integrations"; `sentry.title` "Sentry integration"; `sentry.inbound_title` "Incoming issues (A)"; `sentry.outbound_title` "Resolve & backfill (B — optional, needs A)"; `sentry.webhook_url` "Webhook URL"; `sentry.client_secret` "Client secret (optional — enables signature verification)"; `sentry.api_token` "API token (scope: Issue & Event → Write)"; `sentry.org_slug` "Organization slug"; `sentry.base_url` "Base URL (self-hosted only)"; `sentry.regenerate` "Regenerate URL"; `sentry.reattach` "Re-attach unmatched"; `sentry.reattached` "{n} events attached."; `sentry.unmatched` / `sentry.unmatched_plural` "{n} Sentry event(s) without a project — check your projects' Sentry slugs."; `sentry.guide_a_title` "Setup guide — incoming issues"; `sentry.guide_a_step1` "In Sentry: Settings → Developer Settings → Create New Integration → Internal Integration."; `sentry.guide_a_step2` "Paste the Webhook URL above into the integration's Webhook URL field and enable Webhooks."; `sentry.guide_a_step3` "Check the Issue resource under Webhooks so new issues are sent."; `sentry.guide_a_step4` "Copy the integration's Client Secret into the field above to enable signed delivery (recommended)."; `sentry.guide_a_step5` "In each Pulso project's Settings, set its Sentry project slug. Issues route by that slug."; `sentry.guide_b_title` "Setup guide — resolve & backfill"; `sentry.guide_b_step1` "On the same integration, grant Issue & Event: Write and copy its token into API token."; `sentry.guide_b_step2` "Set your Organization slug (it's in your Sentry URL)."; `sentry.guide_b_step3` "Self-hosted Sentry only: set the Base URL (https://your-host)."
- [ ] **Step 7: run** `pytest tests/test_account_integrations.py tests/test_i18n.py -q` → PASS.
- [ ] **Step 8: Commit** `feat(sentry): owner integrations page with in-app setup guides + unmatched re-attach`

---

### Task 9: Docs + full local gate

**Files:**
- Modify: `CLAUDE.md` (webhooks row, migrations list, MCP section unchanged, Key concepts incident line, Deploy optional-secrets note), `README.md` (if it documents Sentry env vars — align).

- [ ] **Step 1**: CLAUDE.md — migrations line `head = v0017` + entry; `webhooks/` row mentions tokened per-account route + slug routing; Key concepts "Incidents" line gains "routed per project by Sentry slug via the account connection (`/account/integrations`)"; mark `SENTRY_CLIENT_SECRET`/`SENTRY_API_TOKEN`/`SENTRY_ORG` env vars as legacy fallback.
- [ ] **Step 2**: full gate:

```bash
ruff check app/ tests/
python -m mypy app/
TEST_DATABASE_URL=... DEBUG=true SECRET_KEY=any-test-secret python -m pytest tests/ -q
```

Expected: 0 ruff errors, mypy clean, all tests pass, coverage ≥90%.
- [ ] **Step 3: Commit** `docs(sentry): CLAUDE.md — v0017 + per-account sentry connection`

---

### Task 10: PR → CI → merge → tag deploy

- [ ] Push `feat/sentry-per-project`; open PR to `main` (gh CLI), body summarizes spec.
- [ ] Watch CI to green (fix anything that CI's pgvector/pg16 surfaces).
- [ ] Merge PR; pull main; tag `v2026.07.10-2` and push tag (triggers deploy.yml → GHCR → SSH → alembic upgrade head).
- [ ] Post-deploy sanity: `curl -s -o /dev/null -w "%{http_code}" https://<prod-host>/` → 303; unknown-token probe `POST /webhooks/sentry/xyz` → 404.
