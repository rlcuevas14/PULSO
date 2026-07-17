# Pulso — Multi-account / multi-user design

**Date:** 2026-06-29
**Status:** Approved (brainstorming) → implementation
**Branch:** `feat/multi-account`

## Goal

Turn Pulso from single-user into multi-**account** / multi-**user**:

- An **account** is an independent set of projects, isolated from other accounts, starting clean.
- Multiple **users** per account.
- The account **owner** can create collaborator users and grant each access to one or more projects.
- Accounts are fully isolated — a user of account A can never see account B's data.

Today the model is flat: `users` (global unique email, no account), `projects` (global unique slug, no account), `api_tokens` (project_id + created_by). Everything is single-tenant.

## Decisions (from brainstorming)

1. **One user → one account.** `users.account_id` (FK, NOT NULL). No cross-account membership. (A person who collaborates with two owners needs two logins — acceptable for now; A→B upgrade later is additive.)
2. **Operator-provisioned accounts.** A super-admin (the instance operator) creates accounts. No public signup *yet*. Account creation lives in a reusable service `create_account(...)` so a future public `/signup` (the `pulso.io` / `pulso.ai` dream) calls the same function — no rearchitecture.
3. **Two access levels per project:** `viewer` (read) / `editor` (read+write), assigned per project via a matrix. Grant table `project_members(user_id, project_id, role)`.
4. **MCP token scope ≤ your role on the project.** Editor → can mint read|write tokens; viewer → read only; owner → all. Enforced at token creation.

## Approach (chosen: A — account on top of the existing project layer)

The hard work is already done: the multiproject refactor (Chunk 2) put `project_id` on every domain table and `project_id` filters throughout `mcp/tools.py`. An account is just **a group of projects with an owner**, so the isolation unit stays the project; the account only groups.

- New `accounts` table; `account_id` only on `users` and `projects`.
- `items`, `scopes`, `threads`, `sentry_issues`, `agent_runs` are **untouched** — they stay project-scoped and inherit the account transitively.
- Access reduces to one question: *"which projects can this user see?"* (owner → all in account; member → granted projects).

Rejected: **B** (denormalize `account_id` onto every table — redundant tenancy column + leak risk); **C** (Postgres RLS — strongest guarantee but heavy with async SQLAlchemy + in-process worker + hand-rolled MCP; premature).

## Data model

### New table `accounts`
| column | type | notes |
|---|---|---|
| id | uuid pk | |
| name | text NOT NULL | e.g. "Acme Inc" |
| slug | text UNIQUE NOT NULL | for future `pulso.io/{slug}` URLs |
| is_active | bool default true | super-admin can deactivate |
| created_at, updated_at | timestamptz | |

### `users` (changes)
- **+** `account_id` uuid FK `accounts(id)` ON DELETE CASCADE, NOT NULL
- **+** `account_role` text — `ACCOUNT_ROLES = {owner, member}`, default `member`
- **+** `is_superadmin` bool default false (instance operator)
- **~** existing `role` (USER_ROLES) is migrated into `account_role` then dropped
- email stays globally unique (one identity = one login)

### New table `project_members` (grant matrix)
| column | type | notes |
|---|---|---|
| user_id | uuid FK users(CASCADE) | |
| project_id | uuid FK projects(CASCADE) | |
| role | text `PROJECT_MEMBER_ROLES = {viewer, editor}` | |
| created_at | timestamptz | |
| | UNIQUE(user_id, project_id) | |

Owners need **no** rows here — `account_role=owner` ⇒ implicit editor on every project of their account. The matrix is only for members.

### `projects` (changes)
- **+** `account_id` uuid FK `accounts(id)` ON DELETE CASCADE, NOT NULL
- **~** slug uniqueness: `UNIQUE(slug)` → `UNIQUE(account_id, slug)` (two accounts may each have a "web" project)

### `api_tokens`
No structural change. `project_id` + `created_by` already present; account derived via project. The "scope ≤ role" rule is service-level, enforced at mint time.

### Enums (`app/enums.py`)
- `ACCOUNT_ROLES = ("owner", "member")`
- `PROJECT_MEMBER_ROLES = ("viewer", "editor")`

## Access enforcement (single chokepoint)

New `app/projects/access.py`:

```python
async def accessible_project_ids(db, user) -> set[UUID]:
    if user.account_role == "owner":
        return {ids of projects where account_id == user.account_id}
    return {pm.project_id for pm in project_members where user_id == user.id}

async def require_project_access(db, user, project_id, *, need_write=False):
    # 403 if project_id not in accessible_project_ids(user)
    # 403 if need_write and the user's role on that project is viewer
    # owner always passes (with write)
```

- **UI/REST**: every listing uses `accessible_project_ids`; every single-project endpoint uses `require_project_access`. The navbar project selector shows only accessible projects. Switching `current_project_id` validates access.
- **MCP**: almost unchanged. A token is bound to one project; the existing failsafe (`token.project_id` required + `project_id` filters in `tools.py`) already isolates. "Scope ≤ role" is checked when **minting** a token.
- **Super-admin**: separate surface to manage accounts/owners. Does **not** browse other accounts' backlog data (no impersonation — YAGNI). Account-data isolation applies to the super-admin in the normal app too.

Rationale: the #1 multi-tenancy risk is a forgotten `WHERE`. Centralizing in two functions means an isolation audit reviews two functions, not 40 endpoints. The most-exposed vector (agents with tokens) inherits isolation with no new code because MCP was already project-isolated.

## Super-admin & account creation

- `is_superadmin` users get `/admin/accounts`: list accounts + "Create account" form (account name, owner email, owner name, temp password).
- Reusable service: `create_account(db, name, owner_email, owner_name, password) -> (Account, User)` — creates the Account + owner `User(account_role="owner")` in one transaction. The future public `/signup` calls this same function.
- The first-run `/setup` wizard creates the **first account + its owner, marked `is_superadmin=true`** (the operator).
- Super-admin capabilities: create / list / deactivate accounts. No impersonation.

## Member matrix & token UX

- Owner has `/account/members`:
  - **Create collaborator**: name, email, temp password → `User(account_role="member")` in the owner's account (same pattern the super-admin uses for owners). No email/invite infra yet (YAGNI).
  - **Matrix**: rows = members, columns = projects, cell = `none / viewer / editor` (dropdown) → writes `project_members`.
- **Tokens** at `/projects/{slug}/settings`:
  - Available to the owner (any project) and members with a grant on that project.
  - Scope offered by role: editor → read|write; viewer → read only.
  - Owner sees/revokes **all** tokens in the account; a member manages tokens they created.
- The `/admin` legacy token page is updated/removed so project-scoped token creation is the only path (it already must set `project_id`).

## Migration `v0012` (additive, one-way, no data loss)

1. Create `accounts` + `project_members`; add columns to `users`/`projects`; swap slug index to `UNIQUE(account_id, slug)`.
2. If any users/projects exist (your live instance):
   - Create one **default account** (name from env `DEFAULT_ACCOUNT_NAME`, fallback "Default"; slug derived).
   - `UPDATE projects SET account_id = default`.
   - `UPDATE users SET account_id = default`.
   - The earliest-created (or `admin`-role) user → `account_role="owner"` + `is_superadmin=true`; any others → `member`.
   - `api_tokens` untouched (their `project_id` now hangs off an account-bearing project).
3. If the instance is empty (fresh install), the migration adds structure only; `/setup` creates the first account.

Idempotent, runs on the next deploy, zero manual steps. Pre-migration `pg_dump` added to the deploy runbook; the existing `backup-pre-multiproject-final.sql.gz` on the server is the safety net.

## MCP impact

Minimal. Tools stay project-scoped. Changes:
- Token mint endpoint enforces scope ≤ minting-user's role on that project.
- No change to `tools.py` query filtering (already `project_id`-scoped).
- New tools appear only after restarting Claude Code (unchanged behavior).

## Forward-compatibility (pulso.io / pulso.ai)

- `create_account()` is provider-agnostic → a future public `/signup` route is purely additive.
- `accounts.slug` + `UNIQUE(account_id, slug)` already support multi-tenant URLs.
- Billing/plan columns deliberately omitted now (YAGNI); added when SaaS exists.

## Test plan & coverage

Fold the stale-test repair (broken since the English rename, masked by ruff) into this work:
- Fix fixtures: `_token` creates **account + project + project-scoped token**; add `_owner` / `_member` helpers.
- Settle drift: English tool names, arg `scope_name`→`area_name`, English assertions.
- **New tests**:
  - Account isolation: user of A cannot read/list/mutate B's projects, items, threads, incidents (UI, REST, MCP).
  - Grants: viewer cannot write; editor can; owner has implicit editor everywhere in-account.
  - Token scope ≤ role: viewer cannot mint write token; editor can.
  - Super-admin: create account → owner can log in to a clean account.
  - Owner: create member + assign matrix roles.
  - Migration: existing single-user data lands in one account with the operator as owner+superadmin.
- **Target: CI green with ≥90% coverage** (`pytest --cov=app --cov-fail-under=90`).

## Out of scope (YAGNI)

- Public self-signup, email/invite flows, password reset emails.
- Cross-account user membership.
- Super-admin impersonation of accounts.
- Billing / plans / quotas.
- More than 2 per-project roles; more than {owner, member} account roles.

## Deliverable sequence

Account → access enforcement → migration → super-admin UI → member matrix + token UX → test modernization + new coverage → CI ≥90% → PR → tag/deploy.
