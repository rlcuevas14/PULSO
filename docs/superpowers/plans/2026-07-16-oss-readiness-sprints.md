# OSS Readiness (MIT) — Two-Sprint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close all 12 findings of the 2026-07-16 OSS audit so PULSO is a credible, legally sound MIT-licensed open-source project.

**Architecture:** No product architecture changes. Sprint 1 makes the project legally OSS and honest to a stranger (license, docs truth, security fail-fast, community files). Sprint 2 removes the "personal tool" heritage from the public contract (privacy scrub, English-only API surface, reproducible builds, fork-friendly CI).

**Tech Stack:** Existing stack (FastAPI, SQLAlchemy async, Alembic, pytest, GitHub Actions, hatchling).

## Global Constraints

- Everything in the repo is written in **English** (code, comments, docs, commit messages) — owner directive 2026-07-16.
- Every behavior change ships with tests; gates before any tag: `ruff check app/ tests/` · `python -m mypy app/` · `python -m pytest tests/ -q` (needs `TEST_DATABASE_URL`, `DEBUG=true`, `SECRET_KEY`).
- UI strings only via the three i18n catalogs (`app/i18n/locales/{en,es,fr}.json`) — `tests/test_i18n.py` enforces lockstep.
- Trunk-based on `main`; production deploy only via `v*` tag.
- MCP is a public contract: enum/tool changes need a migration + docs update, no silent breaks.

---

## Sprint 1 — "Legally OSS & honest to strangers"

**Status: Tasks 1-5 executed 2026-07-16 in-session (uncommitted, gates green: 342 tests, ruff, mypy, PEP 639 metadata dry-run). Task 6 needs the owner's GitHub account.**

### Task 1: MIT license + package identity ✅

**Files:** Create: `LICENSE` · Modify: `pyproject.toml`, `README.md`

- [x] `LICENSE` at repo root with canonical MIT text, `Copyright (c) 2026 Rodolfo Cuevas`.
- [x] `pyproject.toml`: `name = "pulso"` (was the legacy company-prefixed name), `version = "2026.7.10"` (tracks latest deployed tag; bump manually per release), `description`, `readme`, `license = "MIT"`, `license-files = ["LICENSE"]` (PEP 639), `authors`, `[project.urls]`.
- [x] README: MIT badge under the title; `## License` links `LICENSE`.
- [x] Verified: `pip install --no-deps --dry-run .` builds metadata cleanly.

### Task 2: SECRET_KEY fail-fast honors the shipped placeholder (TDD) ✅

**Files:** Create: `tests/test_config.py` · Modify: `app/config.py`, `.env.example`

- [x] RED: 5 tests in `tests/test_config.py` — production rejects `change-me`, rejects legacy placeholder, rejects <32 chars, accepts 64-char key, debug allows placeholders. Watched the 2 new behaviors fail.
- [x] GREEN: `_PLACEHOLDER_SECRETS = {"dev-secret-change-in-production", "change-me"}` + `_MIN_SECRET_LENGTH = 32` enforced when `DEBUG=false`.
- [x] `.env.example` comment now states the real rule (placeholder OR <32 chars aborts).
- [ ] **Owner, before the next deploy:** confirm the production `SECRET_KEY` in `/opt/pulso/.env` is ≥32 chars, or the app will (correctly) refuse to boot.

### Task 3: README truth pass ✅

**Files:** Modify: `README.md`

- [x] `pip install -r requirements.txt` → `pip install -e ".[dev]"` (the file never existed).
- [x] Test command now includes `DEBUG=true SECRET_KEY=any-test-secret`.
- [x] Deploy secrets corrected to `VM_HOST` / `VM_USER` / `VM_SSH_KEY`; noted GHCR auth uses built-in `GITHUB_TOKEN` and the SSH half is self-hoster-specific.
- [x] Quick start notes the compose file pulls the public GHCR image (no local build).
- [x] Configuration table gains `DATABASE_URL`, `DEBUG`, `PORT`, `IMAGE_TAG`; same vars added (commented) to `.env.example`.

### Task 4: Community health files ✅

**Files:** Create: `SECURITY.md`, `CONTRIBUTING.md`, `.github/ISSUE_TEMPLATE/bug_report.md`, `.github/PULL_REQUEST_TEMPLATE.md`

- [x] `SECURITY.md`: private reporting via GitHub advisory, latest-tag-only support, expectations.
- [x] `CONTRIBUTING.md`: setup, test env vars + dirty-DB reset, the three gates, 90% coverage note, conventions (English-only, i18n lockstep, ItemEvent audit, design tokens, `hx-post` on 204 handlers, LLM via `app/ai/llm.py`).
- [x] Minimal bug report + PR checklist templates.
- [x] README links CONTRIBUTING + SECURITY.

### Task 5: Product docs freshness ✅

**Files:** Modify: `README.md`, `docs/MCP.md`

- [x] "17 MCP tools" → **26** in both files; 9 Management/PMO tools documented with real signatures taken from `app/mcp/server.py:249-334`.

### Task 6: GitHub-side actions (owner account required)

**Files:** none (GitHub settings) — commands runnable by owner or with approved `gh` access.

- [ ] **Step 1:** Topics + homepage:
```bash
gh repo edit rlcuevas14/PULSO \
  --add-topic fastapi --add-topic mcp --add-topic backlog --add-topic self-hosted \
  --add-topic claude --add-topic htmx --add-topic postgres \
  --homepage "https://github.com/rlcuevas14/PULSO"
```
- [ ] **Step 2:** Enable **Private vulnerability reporting**: repo → Settings → Code security → "Private vulnerability reporting" → Enable (SECURITY.md already points there).
- [ ] **Step 3:** Commit Sprint 1, tag, and publish the **first GitHub Release**:
```bash
git tag -a v2026.07.16-1 -m "OSS readiness: MIT license, security fail-fast, community files, honest docs"
git push origin v2026.07.16-1
gh release create v2026.07.16-1 --generate-notes --title "v2026.07.16-1 — MIT licensed"
```
Expected: release visible on the repo; GitHub now shows "MIT license" in the sidebar.
- [ ] **Step 4:** Adopt the habit: every future `v*` tag gets `gh release create <tag> --generate-notes`.

---

## Sprint 2 — "Serious OSS product"

**Status update (2026-07-16, same session):** Tasks 7–13 executed. Remaining owner
actions: Task 6 (GitHub settings + first Release) and setting the repository variable
`DEPLOY_ENABLED=true` so the split deploy job keeps deploying production (Task 11).

### Task 7: Privacy scrub of tracked docs

**Files:** Modify/Delete under `docs/superpowers/` and root. Known exposures (from audit):
- `docs/superpowers/plans/2026-07-05-pulso-ui-redesign.md:471` and `docs/superpowers/specs/2026-07-03-pulso-ui-redesign-design.md:106` — personal email.
- `docs/superpowers/plans/2026-07-10-per-project-sentry.md:1029` — production domain + sanity-check commands.
- `docs/superpowers/specs/2026-06-16-pulso-open-source-multiproject-design.md` — real client/company names.
- `docs/superpowers/specs/2026-06-12-pulso-mcp-threads.md` — legacy private domain.
- Root: `DESIGN-template.md` (internal process, 26 KB).

- [ ] **Step 1 (owner decision):** choose per file: delete from repo, move to a private location, or scrub in place. Default recommendation: keep `docs/superpowers/` structure (it documents design honestly) but scrub emails/domains/client names; move `DESIGN-template.md` to `docs/`.
- [x] **Step 2:** run the sweep (maintainer email, private prod/legacy domains, client names) and fix every hit with neutral placeholders (`<maintainer-email>`, `pulso.example.com`, `acme`/`project-a`). Zero hits after the fix (2026-07-16).
- [ ] **Step 3:** accept that git history already published these strings — decide consciously (history rewrite is NOT recommended; treat email/domains as public and rely on HEAD hygiene).
- [ ] **Step 4:** commit `docs: scrub personal and client data from tracked docs`.

### Task 8: English thread-stage values (migration v0018)

The last Spanish enum in the public MCP contract. Mapping (old → new): `idea→idea`, `investigacion→research`, `historias→stories`, `spec→spec`, `en-desarrollo→in-development`, `review→review`, `hecho→done`, `descartado→discarded`.

**Files:** Create: `migrations/versions/v0018_english_thread_stages.py`, test additions in `tests/test_mcp_tools.py` · Modify: `app/threads/models.py` + wherever stages are defined (`grep -rn "investigacion" app/ tests/` — expect `app/threads/`, `app/mcp/`, `app/templates/hilos*.html`, `tests/test_i18n.py` DYNAMIC_PREFIXES, i18n `stage.*` keys ×3 catalogs), `docs/MCP.md` §4, `CLAUDE.md`.

- [ ] **Step 1 (RED):** add a test asserting `pulso_thread_create` returns `stage == "idea"` and `pulso_thread_advance` moves it to `"research"` (English). Run → FAIL (`investigacion`).
- [ ] **Step 2:** migration v0018, mirroring v0011's pattern — data update on both tables:
```python
STAGES = {"investigacion": "research", "historias": "stories",
          "en-desarrollo": "in-development", "hecho": "done", "descartado": "discarded"}

def upgrade() -> None:
    for old, new in STAGES.items():
        op.execute(f"UPDATE threads SET stage = '{new}' WHERE stage = '{old}'")
        op.execute(f"UPDATE thread_artifacts SET stage = '{new}' WHERE stage = '{old}'")

def downgrade() -> None:
    for old, new in STAGES.items():
        op.execute(f"UPDATE threads SET stage = '{old}' WHERE stage = '{new}'")
        op.execute(f"UPDATE thread_artifacts SET stage = '{old}' WHERE stage = '{new}'")
```
- [ ] **Step 3:** rename the stage constants in code (every `grep` hit from Files above), rename i18n keys `stage.investigacion`→`stage.research` etc. in **all three** catalogs, update `DYNAMIC_PREFIXES["stage."]` in `tests/test_i18n.py`.
- [ ] **Step 4 (GREEN):** full gates. Reset the dirty test DB first (`DROP SCHEMA public CASCADE; CREATE SCHEMA public;`).
- [ ] **Step 5:** document the break in `docs/MCP.md` §4 (same "no compatibility shim" policy as v0011) and update `CLAUDE.md` (remove the "Spanish stages" exception).
- [ ] **Step 6:** commit `feat(threads)!: english stage values (v0018) — MCP contract change`.

### Task 9: English route slugs with 301 redirects

**Files:** Modify: `app/ui/router.py` (route decorators `/prioridad`, `/hilos`, `/incidentes`, `/registro`, plus `/ui/hilos/*`, `/ui/incidentes/*` action paths), `app/templates/base.html` (NAV), templates referencing old paths (`grep -rn "prioridad\|/hilos\|/incidentes\|/registro" app/templates/ tests/`), rename `hilos.html`/`hilo_detail.html`/`incidentes.html`/`prioridad.html` (+ partials) for consistency.

- [ ] **Step 1 (RED):** test that `/priority`, `/threads`, `/incidents`, `/archive` return 200 and the old Spanish paths return 301 to them.
- [ ] **Step 2:** rename the page routes; keep one-line legacy routes:
```python
@router.get("/prioridad", include_in_schema=False)
async def _legacy_prioridad() -> RedirectResponse:
    return RedirectResponse("/priority", status_code=301)
```
(HTMX action endpoints `/ui/...` are not bookmarkable — rename without redirects, they ship atomically with the templates that call them.)
- [ ] **Step 3:** update NAV in `base.html`, all `href`/`hx-*` references, and tests.
- [ ] **Step 4:** gates green; commit `feat(ui)!: english route slugs with 301s from spanish paths`.

### Task 10: Dependency lockfile → reproducible image

**Files:** Create: `requirements.lock` · Modify: `Dockerfile`, `.github/workflows/ci.yml`

- [ ] **Step 1:** generate: `pip install pip-tools && pip-compile pyproject.toml -o requirements.lock` (runtime deps only; keep `pyproject.toml` ranges as-is).
- [ ] **Step 2:** Dockerfile installs pinned deps first, then the app:
```dockerfile
COPY pyproject.toml requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY . .
RUN pip install --no-cache-dir --no-deps .
```
- [ ] **Step 3:** CI installs `-r requirements.lock` + `-e ".[dev]" --no-deps`-equivalent so CI tests what the image ships.
- [ ] **Step 4:** build the image locally (`docker build .`) → succeeds; commit `build: lock runtime dependencies for reproducible images`.

### Task 11: Fork-friendly deploy workflow

**Files:** Modify: `.github/workflows/deploy.yml`, `README.md` (one line)

- [ ] **Step 1:** split into two jobs: `build-push` (unconditional) and `deploy` (`needs: build-push`, `if: vars.DEPLOY_ENABLED == 'true'`), moving the SSH steps into `deploy`. Header comment: `# deploy job is self-hoster-specific: set repo variable DEPLOY_ENABLED=true and VM_* secrets.`
- [ ] **Step 2:** owner sets repo variable `DEPLOY_ENABLED=true` on `rlcuevas14/PULSO` so production keeps deploying.
- [ ] **Step 3:** verify by tagging the Sprint 2 release; both jobs run on the canonical repo.

### Task 12: English docstrings + CI step names (opportunistic, no big-bang)

**Files:** Modify: `app/items/service.py`, `app/items/graph.py`, `app/items/relationships.py`, `app/ai/llm.py`, `app/threads/models.py`, `app/mcp/tools.py`, `app/projects/access.py` (entry-point modules first), `.github/workflows/ci.yml` step names, `migrations/versions/v0015_relax_project_id_nullable.py` docstring (reword the legacy-lineage mention neutrally).

- [ ] **Step 1:** translate module docstrings + comments in the listed files (meaning-preserving, no code changes). `python -m pytest tests/ -q` still green (comments only).
- [ ] **Step 2:** commit `docs: english docstrings in entry-point modules + CI step names`.
- [ ] Policy for everything else is already in CONTRIBUTING.md ("new code: English"); translate the rest only when touching a file.

### Task 13: Repo noise cleanup

**Files:** Delete: `assets/` (raw brand export incl. duplicated `assets/assets/`, `.thumbnail`, `support.js` — the app uses `app/static/brand/` copies) · Move: `DESIGN-template.md` → `docs/` (or delete if Task 7 chose removal).

- [ ] **Step 1:** `git rm -r assets/ && git mv DESIGN-template.md docs/` (confirm nothing references `assets/`: `grep -rn "assets/" app/ README.md` → expect only `app/static` hits).
- [ ] **Step 2:** gates + commit `chore: drop raw asset dump, move internal design template`.

---

## Out of scope of these two sprints

- The 2026-07-16 **UX/UI audit** findings (3 critical / 9 high / 15 medium / 10 low) are tracked separately — its top-5 quick wins are small enough to ride along with Sprint 2 if capacity allows.
- Screenshots for the README (audit finding 7b): capture AFTER the UX quick wins land, so the settings screen photographed is the fixed one.
