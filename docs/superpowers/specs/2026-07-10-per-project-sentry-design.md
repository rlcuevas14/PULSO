# Per-project Sentry integration — design spec

**Date:** 2026-07-10
**Status:** Approved design (pending spec review) → next: writing-plans
**Author:** Rodolfo + Claude
**Scope:** Make each Pulso project *optionally* connect to Sentry with minimal friction, in a way that stays OSS-agnostic and correct under multi-account / multi-project tenancy.

---

## 1. Problem & current state

Sentry support exists in the codebase but is **half-built and effectively dead in the multi-project world**:

| Fact | Evidence |
|---|---|
| One **global** webhook `POST /webhooks/sentry`, authenticated by a single instance-wide `SENTRY_CLIENT_SECRET`. | `webhooks/router.py:16-30`, `config.py:24` |
| Ingest **never sets `SentryIssue.project_id`** → every incoming issue lands `project_id = NULL`. | `webhooks/service.py:58-109` |
| Every per-project view filters by `project_id`: incidents page (`WHERE project_id = pid`), home dashboard count, MCP `pulso_context`. | `ui/router.py:934`, `ui/router.py:99`, `mcp/tools.py:182` |
| **⇒ Net effect:** issues arriving today are invisible on *every* project's `/incidentes`. The integration silently does nothing per-project. | — |
| Per-project columns `projects.sentry_client_secret / sentry_api_token / sentry_org` exist (since `v0010`) and the settings form saves them — but **no runtime code reads them**. Grep of reads returns only writes + template render. | `projects/models.py:34-38`, `projects/router.py:112-128` |
| Outbound calls (`resolve_in_sentry`, `fetch_issue_detail`, `fetch_sentry_issues`) hardcode `https://sentry.io` and read the **global** `SENTRY_API_TOKEN`. | `webhooks/service.py:290,295,316,322` |
| Backfill is an owner-only form that pastes `org/project/token` **every time** and also lands `project_id = NULL`. | `ui/router.py:1005-1027` |

So this is not a greenfield feature — it's **finishing and correcting** an existing one.

---

## 2. Goals / non-goals

**Goals**
- G1. A project can *optionally* receive its Sentry issues, correctly attached to that project (`project_id` set), so `/incidentes` + dashboard + MCP work.
- G2. Minimal friction: **one** Sentry setup per account, then **one field** per project.
- G3. OSS-agnostic: no hardcoded host; works with self-hosted Sentry and SaaS region hosts.
- G4. Optional outbound (feature **B**): closing an incident in Pulso can resolve it in Sentry. **A is a prerequisite of B.**
- G5. Step-by-step setup guides that live **inside Pulso** (trilingual, values pre-filled).
- G6. Tenancy-safe: a webhook for account X can only ever write to account X's projects.

**Non-goals (YAGNI for v1)**
- Multiple Sentry orgs per Pulso account (confirmed 1:1 by product owner).
- Two-way comment sync, assignment sync, metric-alert ingestion.
- Auto-promotion of issues to backlog (unchanged: stays manual/triage).
- Editing/among GitHub per-project secret wiring (separately unwired; out of scope).

---

## 3. Model (the decision)

**1 Pulso account ↔ 1 Sentry organization ↔ 1 Sentry Internal Integration.** Per-project routing happens by **Sentry project slug**.

Why this shape (verified against Sentry docs):
- Sentry **Internal Integrations are org-level**; the `issue` resource webhook is an **org-wide firehose** (`issue.created` fires for *every* new issue in the org, no alert rule needed). There is **no** native per-project webhook scoping (open request getsentry/sentry#94731).
- Therefore a *true* per-project webhook URL would cost **one integration per project** — rejected as high-friction.
- Instead: **one account-level webhook URL** (carrying an opaque token) receives the firehose; Pulso routes each issue to a project by matching `data.issue.project.slug` against a per-project slug mapping, **scoped to the account** that owns the token.

### Setup friction (happy path)

**Once per account (owner):** Sentry → Settings → Developer Settings → *Create New Internal Integration* → check the **Issue** resource → paste Pulso's account **Webhook URL** → copy the **Client Secret** into Pulso. (For feature B, grant *Issue & Event: Write* and copy the auto-generated **token** into Pulso.)

**Once per project (editor):** in the project settings, set **Sentry project slug** = the slug shown in Sentry's project URL. Done.

---

## 4. Architecture

### 4.1 Data model

**New table `sentry_connections`** (1 row per account; holds secrets + the indexed webhook token; keeps the hot `accounts` row lean and secrets off the tenancy path):

| Column | Type | Notes |
|---|---|---|
| `id` | uuid pk | |
| `account_id` | uuid FK→accounts, **unique**, on delete cascade | 1:1 with account |
| `webhook_token` | text, **unique**, not null | opaque routing+auth token; embedded in the inbound URL |
| `client_secret` | text nullable | for HMAC verify (signed mode). Null ⇒ unsigned mode. |
| `api_token` | text nullable | outbound (feature B). Null ⇒ B disabled. |
| `org_slug` | text nullable | Sentry org slug (outbound URL) |
| `base_url` | text nullable | default `https://sentry.io`; override for self-hosted / region hosts |
| `created_at`, `updated_at` | timestamptz | |

- `webhook_token` stored **plain** (not hashed): the settings page must re-display the URL on demand. Mitigations: (a) HMAC signing makes the token-alone insufficient in signed mode; (b) a **Regenerate** action rotates it. Documented tradeoff (§8).

**`projects` changes**
- **Add** `sentry_project_slug` text nullable. Constraint `UNIQUE (account_id, sentry_project_slug)` on `projects` — two projects in one account can't claim the same Sentry slug. Postgres treats NULLs as distinct, so unmapped projects (NULL slug) don't collide.
- **Drop** the three vestigial, never-read columns `sentry_client_secret`, `sentry_api_token`, `sentry_org`. They moved to account level; any values there were never functional (no data-loss risk, see §7 migration).
- `github_webhook_secret` left untouched (separate concern).

**`sentry_issues`** — add nullable `account_id` FK→accounts (on delete cascade), set on every tokened-webhook ingest from the connection's account. This is what makes **unmatched** rows tenancy-safe: without it, in a multi-account instance, account X could "steal" account Y's unmatched incidents via Re-attach by claiming the same slug (§4.4). Legacy pre-existing rows keep `account_id = NULL`. `project_id` unchanged (the routing target).

### 4.2 Inbound flow (feature A)

New route: `POST /webhooks/sentry/{token}` (no session; the token + optional HMAC are the auth).

```
POST /webhooks/sentry/{token}
  1. conn = lookup sentry_connections by webhook_token    → unknown token: 404 + log, no processing
  2. if conn.client_secret:                                # signed mode
         verify HMAC-SHA256(client_secret, raw_body) == header "Sentry-Hook-Signature"
         reject (401) on mismatch
     else:                                                 # unsigned mode (legacy plugin)
         token-in-URL is the sole auth
  3. parse payload (dual shape, §4.3) → {sentry_id, title, level, slug, permalink, count, first/last_seen}
  4. project = the project WHERE account_id = conn.account_id AND sentry_project_slug = slug  (≤1 by constraint)
        - found → ingest with project_id = project.id       (existing upsert/dedupe logic)
        - none  → ingest as UNMATCHED (project_id NULL, logged) so the UI can nudge "check slug"
  5. return 200 within <1s (ack fast); triage stays async on the worker (AgentRun, already the case)

Response codes: unknown token → **404**; valid token + bad/missing signature (signed mode) → **401**; valid token + unmatched slug → **200** (parked, §4.4); success → **200**.
```

**Isolation guarantee (G6):** step 4 filters `account_id = conn.account_id`. A webhook token for account X can *only* resolve to a project in X. Even if account Y has a project with the same Sentry slug, it is unreachable. This is the tenancy chokepoint for webhooks (analogous to `projects/access.py`).

**Legacy route** `POST /webhooks/sentry` (global env secret): kept one release, **deprecated**. In a multi-account instance it cannot determine the account, so it stays best-effort single-account only. The in-app guide and all new setup use the tokened URL exclusively.

### 4.3 Payload parsing (both shapes)

Verified: two webhook shapes exist. Parser must handle both (extend the existing `data.issue or issue or payload` logic in `ingest_sentry`):

| Field | `issue` webhook (primary) | `event_alert` webhook |
|---|---|---|
| `Sentry-Hook-Resource` | `issue` | `event_alert` |
| issue id | `data.issue.id` | `data.event.issue_id` |
| title | `data.issue.title` | `data.event.title` |
| level | `data.issue.level` | `data.event.level` |
| project **slug** | `data.issue.project.slug` ✅ | **absent** (only numeric `data.event.project`) |
| permalink | `data.issue.permalink` | `data.event.web_url` |

**Routing rule (v1):** route by **slug**, available only in the `issue` webhook. The in-app guide configures the **Issue** resource, so the happy path always carries the slug. `event_alert` payloads are parsed for data but, lacking a slug, route only when the account has exactly one enabled project (else → UNMATCHED). Numeric-project-id mapping is a documented future extension, not v1.

### 4.4 Unmatched handling

Sentry expects a **200 within ~1s or it counts a timeout** (1000 timeouts/24h ⇒ webhook auto-disabled; **no guaranteed retry**). So we **always 200-ack**, even when we can't route. Unmatched issues are stored `project_id = NULL, account_id = conn.account_id` + a structured log line, and the account integration page surfaces *"N unmatched Sentry events — verify your project slug mappings."* After the user fixes a slug, a one-click **Re-attach unmatched** action matches NULL-project rows by their stored text `project` slug against the account's `sentry_project_slug` mappings, **scoped to `account_id = this account OR account_id IS NULL`** (the NULL-account arm recovers legacy single-account-era rows; safe because they predate multi-account and could only belong to the instance's original account — §7).

### 4.5 Outbound flow (feature B — optional, requires A)

Refactor `resolve_in_sentry`, `fetch_issue_detail`, `fetch_sentry_issues` to accept `(api_token, org_slug, base_url)` resolved from the issue's **account connection** instead of global `settings`:

- Resolve: `PUT {base_url}/api/0/organizations/{org_slug}/issues/{issue_id}/` body `{"status":"resolved"}`, `Authorization: Bearer {api_token}` (scope *event:write*). Documented org-scoped endpoint (the legacy `/api/0/issues/{id}/` still works but is undocumented).
- **429 handling:** honor `Retry-After` (single delayed retry, then give up). Failure never blocks the local close — `resolve_issue` already swallows outbound errors (`service.py:155-163`), keep that.
- **Backfill** (`/ui/incidentes/backfill`): replace the paste-every-time form with the stored account connection — owner triggers "pull `is:unresolved` for this project" using `account.api_token/org_slug/base_url` + the project's slug; ingest sets `project_id`. Stays owner-only.
- Base URL default `https://sentry.io`; per-account override covers self-hosted (`https://<host>`) and SaaS region hosts (`us.sentry.io`, `de.sentry.io`, …).

---

## 5. Permissions & config surface

| Action | Who | Where |
|---|---|---|
| Configure account Sentry connection (secrets, org, base URL, regenerate token) | **owner** (matches existing owner-only backfill) | new **`/account/integrations`** (owner page, sibling of `/account/members`) |
| Set a project's `sentry_project_slug` + enable | **editor+** (self-service) via `require_project_access` write | extend `/projects/{slug}/settings` (replace the dead sentry secret fields with one slug field) |
| Promote / ignore / resolve an incident | editor+ (existing `_guard_row(write=True)`) | `/incidentes` (unchanged) |
| Trigger backfill | owner (unchanged) | `/incidentes` |

Inbound webhook has no session; its authority is the token → account, and it can only write within that account (§4.2).

---

## 6. In-app guides (G5)

Two guides, rendered from i18n catalogs with values interpolated, no external doc dependency:

- **Guide A (inbound)** on `/account/integrations`: create Internal Integration → check **Issue** resource → paste the shown **Webhook URL** (`{base_url_of_pulso}/webhooks/sentry/{token}`) → copy **Client Secret** back → then *"in each project's settings, set its Sentry slug."* Shows the live URL + a copy button + the *unmatched events* nudge.
- **Guide B (outbound)** on the same page, collapsed: grant the integration **Issue & Event: Write**, copy its **token** + **org slug** here; set **base URL** if self-hosted. Then Pulso can resolve issues in Sentry.

Guides use the **verified current** Sentry labels and link out to `docs.sentry.io` for the canonical flow (labels may shift between Sentry UI versions — noted in the guide).

---

## 7. Migration (`v0017`)

1. `CREATE TABLE sentry_connections` (§4.1).
2. `ALTER TABLE projects ADD COLUMN sentry_project_slug text` + unique-per-account constraint; `ALTER TABLE sentry_issues ADD COLUMN account_id uuid REFERENCES accounts(id) ON DELETE CASCADE` (nullable).
3. `ALTER TABLE projects DROP COLUMN sentry_client_secret, sentry_api_token, sentry_org` — vestigial, never read. **Risk note:** the prod settings form *did* let a user type values here (they were saved but never used). Dropping loses those inert strings; the owner re-enters them once at account level. Acceptable (they were non-functional). If desired, a pre-drop `SELECT` can be logged for manual recovery.
4. **Legacy NULL `sentry_issues`:** not auto-attached at migration time (no slug mapping exists yet). Handled post-config by the **Re-attach unmatched** action (§4.4), which matches `sentry_issues.project` (text slug) → `projects.sentry_project_slug` within an account.
5. Head advances `v0016 → v0017`. Remember the dirty-DB reset gotcha for local tests (CLAUDE.md).

Backward-compat: legacy `POST /webhooks/sentry` route + global `settings.sentry_*` remain functional (deprecated) for one release so existing single-account installs don't break mid-upgrade.

---

## 8. Security

- **HMAC** (signed mode): constant-time compare (`hmac.compare_digest`, already used) over **raw** body bytes with the account `client_secret`. Header `Sentry-Hook-Signature`.
- **Unsigned mode** (legacy plugin / no secret): auth is the opaque URL token only. Documented as weaker (an attacker with the URL can inject *noise* incidents, not read data). Recommend signed mode in the guide.
- **Token entropy:** `webhook_token` = `secrets.token_urlsafe(32)`. Plain storage justified by the re-display requirement; **Regenerate** rotates it (invalidates the old URL). Tradeoff explicitly documented in the settings UI.
- **XSS:** untrusted Sentry titles/culprit already sanitized via `_sanitize` (strip tags, length-cap) — keep for both payload shapes.
- **Fast-ack + idempotent:** minimal synchronous work (upsert + enqueue), dedupe by `sentry_issue_id` UNIQUE (already), 200 within 1s to avoid disable-on-failure.
- **`base_url` validation (SSRF hygiene):** owner-entered; accept only `http(s)://host[:port]` with no path/query/fragment (mirrors Sentry's own `system.url-prefix` rule). Low risk (owner-only field) but cheap to enforce at the form/service boundary.
- Secrets never logged. The owner-only connection page displays stored secrets plaintext in the form (same pattern as `projects_settings.html`'s github secret; owner over HTTPS pasting their own secrets).

---

## 9. Components (isolation & boundaries)

- `sentry/connection.py` (new, small): resolve connection by `webhook_token`; resolve `(api_token, org, base_url)` for an issue's account. Single source of truth; keeps `webhooks/service.py` focused.
- `webhooks/service.py`: `ingest_sentry` gains an `account`/`project` resolution step + dual-shape parse; outbound fns take explicit `(token, org, base_url)`.
- `webhooks/router.py`: add tokened route; keep legacy route (deprecated).
- `accounts/` : `SentryConnection` model + service (get/upsert/regenerate) + `/account/integrations` router + template + guide.
- `projects/`: add `sentry_project_slug` to model, settings form, service update; drop dead columns.
- `i18n/locales/{en,es,fr}.json`: `sentry.*` keys (parity enforced by `test_i18n.py`).

Each unit answers: *what it does / how used / what it depends on* — connection resolver depends only on the DB; parser is pure; router is thin.

---

## 10. Test matrix (CI is the gate; ≥90%)

**Inbound**
- token → account resolution; unknown token → 404 + no write.
- signed mode: valid HMAC ingests; tampered body → 401; wrong secret → 401.
- unsigned mode (no client_secret): accepted on token alone.
- payload parse: `issue` shape and `event_alert` shape both extract id/title/level/permalink.
- routing: slug matches a project in the account → `project_id` set; **cross-account slug collision → does NOT leak** (isolation test, G6); unmatched slug → NULL project + `account_id` stamped + logged + 200.
- re-attach tenancy: account X's Re-attach never claims rows stamped `account_id = Y`; NULL-account legacy rows are claimable.
- dedupe: same `sentry_issue_id` twice → one row, `events_count` increments.

**Outbound (B)**
- resolve builds `{base_url}/api/0/organizations/{org}/issues/{id}/` from the account connection; self-hosted base_url honored; 429 + Retry-After handled; outbound failure does not block local close.
- backfill uses stored connection + project slug, sets `project_id`.

**UI / perms / i18n**
- `/account/integrations` owner-only (member → 403); regenerate rotates token.
- project settings: editor sets slug, viewer blocked; unique-per-account slug enforced.
- guide renders with interpolated URL; `test_i18n` parity across en/es/fr.

**Migration**
- upgrade adds/drops columns; re-attach-unmatched matches NULL rows by slug within account.

---

## 11. Rollout

1. Ship migration + code behind no flag (feature is inert until an account configures a connection).
2. Owner configures `/account/integrations` (Guide A) → sets project slugs → optional **Re-attach unmatched** for legacy rows.
3. Verify: a fresh Sentry issue appears on the right project's `/incidentes`.
4. Optionally add API token (Guide B) → resolve-in-Sentry + backfill enabled.
5. Deploy by tag; CI green first (CLAUDE.md deploy flow).

---

## 12. Open items (none blocking)

- `event_alert` numeric-project-id mapping (only if a user insists on alert-rule routing instead of the Issue resource) — future.
- Multiple Sentry orgs per account — out of scope by product decision.
- Auto-purge of long-unmatched NULL rows — optional housekeeping later.
