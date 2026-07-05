# PULSO UI/UX Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reskin all 16 PULSO screens onto a CSS-variable design-token system (Clay-cream light + warm near-black dark, OS-default + remembering toggle), card-launcher home, per-project accent, celebration overlay on completions, brand assets, mobile responsiveness — per `docs/superpowers/specs/2026-07-03-pulso-ui-redesign-design.md`.

**Architecture:** One shared `partials/_head.html` holds the entire theme: inline `tailwind.config` (CDN, `darkMode:'class'`, semantic colors mapped to CSS variables), a `<style>` block with `:root`/`html.dark` variable sets + a small set of `.p-*` component classes, and a blocking no-FOUC theme script. Per-project accent = `--accent`/`--accent-fg` inline on `<html>` from session. Success feedback = pop-once session flash rendered by `base.html`. Only new plumbing: `StaticFiles` mount for brand assets.

**Tech Stack:** FastAPI + Jinja2 + HTMX 2 (CDN) + Tailwind (CDN, inline config — NO Node build). Postgres. Tests: pytest-asyncio + httpx AsyncClient.

## Global Constraints

- **Branch:** all work on `feat/ui-redesign`. Commit per task. One PR at the end.
- **NO new dependencies.** No Node, no npm, no new Python packages. `StaticFiles` is Starlette built-in.
- **Never inline theme hex in templates** — use token utilities (`bg-canvas`, `text-ink`, `border-hairline`, …) or `.p-*` classes. Exceptions: the logo node `#ff4d8b` (fixed brand pink, never themed) and `var(--accent)` inline styles.
- **Never use Tailwind opacity modifiers on semantic tokens** (`bg-canvas/50` breaks — they're CSS vars). Opacity modifiers are allowed ONLY on static hex colors: `brand-*`, `success`, `warning`, `error` (e.g. `bg-brand-ochre/30`, `bg-success/15`).
- **Accent is wayfinding only** — nav underline/bar, focus rings, links, project pill, active ring. NEVER on buttons (buttons are neutral `--primary`).
- **Preserve every existing HTMX interaction** (hx-get/post/target/swap/include, `HX-Refresh` flows) and every form field name/route. This is a reskin: routes, handlers' contracts, and lifecycle logic do not change except where a task explicitly says so.
- **Copy stays as-is** (mixed ES/EN); Spanish routes stay; thread stages stay Spanish.
- **Radii:** Tailwind defaults match the spec — buttons/inputs `rounded-xl` (12px), content cards `rounded-2xl` (16px), home/feature cards `rounded-3xl` (24px), pills `rounded-full`.
- **Verify before commit:** for Python changes run `ruff check app/ tests/`, `python -m mypy app/`, and the named tests with `TEST_DATABASE_URL="postgresql+asyncpg://efrain:efrain@localhost:5432/pulso_test" python -m pytest <files> -q`. Template-only changes: run `tests/test_ui.py` as smoke.
- **Dirty-DB gotcha:** if schema-ish failures appear locally, reset: `DROP SCHEMA public CASCADE; CREATE SCHEMA public;` on `pulso_test`.
- **Canonical status→pill map** (single source, implemented once in `partials/_status_badge.html`, Task 5):
  `idea → bg-brand-lavender/25 text-ink` · `backlog → bg-surface-strong text-body` · `spec → bg-brand-mint/30 text-ink` · `in-progress → bg-brand-ochre/30 text-ink` · `blocked → bg-warning/20 text-warning` · `in-review → bg-brand-peach/30 text-ink` · `done → bg-success/15 text-success` · `discarded → bg-surface-strong text-muted line-through`
- **Canonical priority map:** `p0 → bg-brand-coral text-white` · `p1 → bg-brand-ochre text-ink` · `p2 → bg-surface-strong text-body` · `p3 → bg-surface-strong text-muted`
- **Canonical type→chip tint map:** `bug → bg-brand-coral/20` · `feature → bg-brand-teal/15` · `tech-debt → bg-brand-ochre/25` · `infra → bg-brand-lavender/25` · `docs → bg-brand-mint/30` · `ops → bg-brand-peach/30` · `security → bg-warning/20` · `product → bg-brand-pink/20` · `idea → bg-brand-lavender/25` — all with `text-ink`.
- **Mechanical class map** (apply when reskinning any template; old → new):
  `bg-gray-50 → (delete; body is bg-canvas)` · `bg-white rounded(-lg) shadow(-sm)? (border(-gray-200)?)? → p-card` · `bg-white → bg-surface-card` · `border-gray-200|300 → border-hairline` · `divide-gray-200 → divide-hairline` · `text-gray-900|800 → text-ink` · `text-gray-700|600 → text-body` · `text-gray-500|400 → text-muted` · `bg-gray-100|200 (chips/lanes) → bg-surface-strong` · `hover:bg-gray-50|100 → hover:bg-surface-strong` · primary buttons (`bg-gray-900|blue-600|indigo-600|green-600 text-white …`) → `p-btn p-btn-primary` (small: add `p-btn-sm`) · secondary/outline buttons → `p-btn p-btn-ghost` · text inputs/selects/textareas → `p-input` (inline small selects: `p-input p-input-sm`) · form labels → `p-label` · blue help banners (`bg-blue-50 border-blue-200 text-blue-800`) → `{{ hint('<screen>', '<text>') }}` macro · green success boxes (`bg-green-50…`) → `bg-success/10 border border-success/30 text-success rounded-xl` · red error boxes → `bg-error/10 border border-error/30 text-error rounded-xl` · amber boxes → `bg-warning/10 border border-warning/30 text-warning rounded-xl`.

---

## Sprint 1 — Foundation

### Task 1: Static assets mount + brand files

**Files:**
- Create: `app/static/brand/` (12 files copied from `assets/`), `app/static/manifest.webmanifest`
- Modify: `app/main.py` (mount), `.gitignore` check (ensure `app/static` not ignored)
- Also commit: `assets/` (source kit) and `DESIGN-template.md` (reference) — currently untracked
- Test: `tests/test_ui.py` (append one test)

**Interfaces:**
- Produces: URL paths used by later tasks: `/static/brand/pulso-favicon-16.svg`, `/static/brand/favicon-32.png`, `/static/brand/apple-touch-180.png`, `/static/manifest.webmanifest`, `/static/brand/avatar-cream-800.png`, `/static/brand/avatar-dark-800.png`.

- [ ] **Step 1: Copy brand files**

```bash
mkdir -p app/static/brand
cp assets/pulso-icon-black.svg assets/pulso-icon-cream.svg assets/pulso-icon-mono.svg assets/pulso-favicon-16.svg app/static/brand/
cp assets/assets/pulso-spinner.svg app/static/brand/
cp assets/assets/png/favicon-16.png assets/assets/png/favicon-32.png assets/assets/png/apple-touch-180.png assets/assets/png/app-icon-512.png assets/assets/png/app-icon-1024.png assets/assets/png/avatar-cream-800.png assets/assets/png/avatar-dark-800.png app/static/brand/
```

- [ ] **Step 2: Write `app/static/manifest.webmanifest`**

```json
{
  "name": "Pulso",
  "short_name": "Pulso",
  "description": "The backlog your agent reads and writes.",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#fffaf0",
  "theme_color": "#fffaf0",
  "icons": [
    { "src": "/static/brand/app-icon-512.png", "sizes": "512x512", "type": "image/png" },
    { "src": "/static/brand/app-icon-1024.png", "sizes": "1024x1024", "type": "image/png" }
  ]
}
```

- [ ] **Step 3: Mount static in `app/main.py`** — inside `create_app()`, right after `app = FastAPI(...)`:

```python
from fastapi.staticfiles import StaticFiles  # top of create_app's import block
app.mount("/static", StaticFiles(directory="app/static"), name="static")
```

- [ ] **Step 4: Append test to `tests/test_ui.py`**

```python
@pytest.mark.asyncio
async def test_static_brand_assets_served(client: AsyncClient):
    r = await client.get("/static/brand/pulso-favicon-16.svg")
    assert r.status_code == 200 and "svg" in r.headers["content-type"]
    r = await client.get("/static/manifest.webmanifest")
    assert r.status_code == 200
```

- [ ] **Step 5: Run** `pytest tests/test_ui.py::test_static_brand_assets_served -v` → PASS. Then `ruff check app/ tests/` + `python -m mypy app/` → clean.

- [ ] **Step 6: Commit**

```bash
git add app/static app/main.py tests/test_ui.py assets DESIGN-template.md
git commit -m "feat(ui): mount /static and ship Pulso brand assets"
```

---

### Task 2: `_head.html` — tokens, Tailwind config, component classes, no-FOUC

**Files:**
- Create: `app/templates/partials/_head.html`
- Modify: `app/templates/base.html` `<head>` only (replace lines 3–11 with the include; body class `bg-gray-50` → `bg-canvas text-body font-sans`; `<html lang="es">` gains the accent style attr)
- Test: existing `tests/test_ui.py::test_dashboard_and_screens_render` as smoke

**Interfaces:**
- Consumes: `/static/brand/*` paths (Task 1).
- Produces: token utilities (`bg-canvas`, `bg-surface-soft`, `bg-surface-card`, `bg-surface-strong`, `text-ink`, `text-body`, `text-muted`, `border-hairline`, `bg-primary`, `text-on-primary`, `bg-brand-{pink,teal,lavender,peach,ochre,coral,mint}`, `bg-success/warning/error` + text variants); component classes `.p-card .p-btn .p-btn-primary .p-btn-ghost .p-btn-sm .p-input .p-input-sm .p-label .p-pill .p-menu .p-menu-panel .p-menu-item .p-navlink .p-navlink-active .p-homecard .p-spin`; keyframes `pulso-dash pulso-blip p-pop p-draw`; JS `toggleTheme()`. Every later task depends on these names.

- [ ] **Step 1: Write `app/templates/partials/_head.html`** (complete file):

```html
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="theme-color" content="#fffaf0">
<title>{% block title %}Pulso{% endblock %}</title>
<link rel="icon" type="image/svg+xml" href="/static/brand/pulso-favicon-16.svg">
<link rel="icon" type="image/png" sizes="32x32" href="/static/brand/favicon-32.png">
<link rel="apple-touch-icon" href="/static/brand/apple-touch-180.png">
<link rel="manifest" href="/static/manifest.webmanifest">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
{# No-FOUC: set dark class + theme-color BEFORE first paint. Must stay inline & blocking. #}
<script>
  (function () {
    try {
      var t = localStorage.getItem("pulso-theme");
      var dark = t === "dark" || (!t && window.matchMedia("(prefers-color-scheme: dark)").matches);
      if (dark) document.documentElement.classList.add("dark");
      var m = document.querySelector('meta[name="theme-color"]');
      if (m && dark) m.content = "#0e0e10";
    } catch (e) { /* sin localStorage: queda light */ }
  })();
  function toggleTheme() {
    var dark = document.documentElement.classList.toggle("dark");
    try { localStorage.setItem("pulso-theme", dark ? "dark" : "light"); } catch (e) {}
    var m = document.querySelector('meta[name="theme-color"]');
    if (m) m.content = dark ? "#0e0e10" : "#fffaf0";
  }
</script>
{# TODO(pre-existing): vendorizar/compilar Tailwind para prod (el CDN es solo-dev). #}
<script src="https://cdn.tailwindcss.com"></script>
<script>
  tailwind.config = {
    darkMode: "class",
    theme: {
      extend: {
        colors: {
          canvas: "var(--canvas)",
          "surface-soft": "var(--surface-soft)",
          "surface-card": "var(--surface-card)",
          "surface-strong": "var(--surface-strong)",
          ink: "var(--ink)",
          body: "var(--body)",
          muted: "var(--muted)",
          hairline: "var(--hairline)",
          primary: "var(--primary)",
          "on-primary": "var(--on-primary)",
          accent: "var(--accent)",
          "accent-fg": "var(--accent-fg)",
          "brand-pink": "#ff4d8b",
          "brand-teal": "#1a3a3a",
          "brand-lavender": "#b8a4ed",
          "brand-peach": "#ffb084",
          "brand-ochre": "#e8b94a",
          "brand-coral": "#ff6b5a",
          "brand-mint": "#a4d4c5",
          success: "#22c55e",
          warning: "#f59e0b",
          error: "#ef4444"
        },
        fontFamily: { sans: ["Inter", "system-ui", "sans-serif"] }
      }
    }
  };
</script>
<style type="text/css">
  :root {
    --canvas:#fffaf0; --surface-soft:#faf5e8; --surface-card:#f5f0e0; --surface-strong:#ebe6d6;
    --ink:#0a0a0a; --body:#3a3a3a; --muted:#8a8578; --hairline:#eae5d9;
    --primary:#0a0a0a; --on-primary:#ffffff;
    --accent:#6366f1; --accent-fg:#ffffff;
  }
  html.dark {
    --canvas:#0e0e10; --surface-soft:#17171a; --surface-card:#1c1c20; --surface-strong:#242429;
    --ink:#f5f3ee; --body:#c9c7c1; --muted:#8f8d88; --hairline:#2a2a30;
    --primary:#f5f3ee; --on-primary:#0e0e10;
  }
  /* ---- component kit ---- */
  .p-card { background:var(--surface-card); border:1px solid var(--hairline); border-radius:16px; }
  html.dark .p-card { box-shadow:0 1px 3px rgba(0,0,0,.35); }
  .p-btn { display:inline-flex; align-items:center; justify-content:center; gap:.5rem; height:44px;
           padding:0 20px; border-radius:12px; font-size:14px; font-weight:600; line-height:1;
           cursor:pointer; border:1px solid transparent; text-decoration:none;
           transition:opacity .15s, transform .1s; }
  .p-btn:hover { opacity:.85; } .p-btn:active { transform:scale(.98); }
  .p-btn-primary { background:var(--primary); color:var(--on-primary); }
  .p-btn-ghost { background:transparent; color:var(--ink); border-color:var(--hairline); }
  .p-btn-sm { height:34px; padding:0 12px; font-size:13px; border-radius:10px; }
  .p-input { height:44px; padding:0 14px; border-radius:12px; background:var(--canvas);
             border:1px solid var(--hairline); color:var(--ink); font-size:14px; width:100%; }
  select.p-input { padding-right:1.75rem; }
  textarea.p-input { height:auto; min-height:96px; padding:10px 14px; }
  .p-input:focus { outline:2px solid var(--accent); outline-offset:1px; }
  .p-input-sm { height:32px; font-size:12px; border-radius:8px; padding:0 8px; width:auto; }
  .p-label { display:block; font-size:12px; font-weight:600; letter-spacing:.08em;
             text-transform:uppercase; color:var(--muted); margin-bottom:.35rem; }
  .p-pill { display:inline-flex; align-items:center; gap:.3rem; padding:2px 10px;
            border-radius:9999px; font-size:12px; font-weight:500; white-space:nowrap; }
  .p-navlink { color:var(--muted); font-size:14px; font-weight:500; padding:20px 2px 17px;
               border-bottom:3px solid transparent; text-decoration:none; }
  .p-navlink:hover { color:var(--ink); }
  .p-navlink-active { color:var(--ink); border-bottom-color:var(--accent); }
  .p-menu { position:relative; }
  .p-menu > summary { list-style:none; cursor:pointer; }
  .p-menu > summary::-webkit-details-marker { display:none; }
  .p-menu-panel { position:absolute; right:0; top:calc(100% + 8px); min-width:220px; z-index:60;
                  background:var(--surface-card); border:1px solid var(--hairline);
                  border-radius:12px; padding:6px; box-shadow:0 8px 24px rgba(0,0,0,.12); }
  .p-menu-item { display:block; width:100%; text-align:left; padding:8px 10px; border-radius:8px;
                 font-size:14px; color:var(--body); text-decoration:none; }
  .p-menu-item:hover { background:var(--surface-strong); color:var(--ink); }
  .p-homecard { border-radius:24px; padding:24px; min-height:150px; display:flex;
                flex-direction:column; justify-content:space-between; text-decoration:none;
                transition:transform .15s ease, box-shadow .15s ease; }
  .p-homecard:hover { transform:translateY(-3px); box-shadow:0 10px 30px rgba(0,0,0,.15); }
  /* branded spinner: hidden until htmx marks the request */
  .p-spin { display:none; }
  .htmx-request .p-spin, .htmx-request.p-spin { display:inline-block; }
  .p-spin path { stroke-dasharray:61; animation:pulso-dash 1.6s ease-in-out infinite; }
  .p-spin circle { animation:pulso-blip 1.6s linear infinite; }
  @keyframes pulso-dash { 0%{stroke-dashoffset:61} 50%{stroke-dashoffset:0} 100%{stroke-dashoffset:-61} }
  @keyframes pulso-blip { 0%,40%,100%{opacity:.35} 55%{opacity:1} }
  @keyframes p-pop { from{opacity:0;transform:scale(.9)} to{opacity:1;transform:scale(1)} }
  @keyframes p-draw { to{stroke-dashoffset:0} }
  @media (prefers-reduced-motion: reduce) {
    *, ::before, ::after { animation-duration:.01ms !important; animation-iteration-count:1 !important;
                           transition-duration:.01ms !important; }
  }
</style>
<script src="https://unpkg.com/htmx.org@2.0.0/dist/htmx.min.js"
        integrity="sha384-wS5l5IKJBvK6sPTKa2WZ1js3d947pvWXbPJ1OmWfEuxLgeHcEbjUUA5i9V5ZkpCw"
        crossorigin="anonymous"></script>
```

- [ ] **Step 2: Adopt in `base.html`** — replace the whole current `<head>` (lines 3–12) with:

```html
<head>
  {% include "partials/_head.html" %}
</head>
```

Change `<html lang="es">` to:

```html
<html lang="es" style="--accent: {{ request.session.get('current_project_color') or '#6366f1' }}; --accent-fg: {{ accent_fg(request.session.get('current_project_color')) }}">
```

(`accent_fg` global lands in Task 3 — Tasks 2+3 are committed together.)
Change `<body class="bg-gray-50 min-h-screen">` to `<body class="bg-canvas text-body font-sans min-h-screen flex flex-col">` and wrap: `<main class="max-w-7xl mx-auto px-4 sm:px-6 py-6 w-full flex-1">`.

**Note:** `{% block title %}` moves into `_head.html` via the include — Jinja blocks work inside includes only with `{% include ... %}` inheriting context; blocks do NOT propagate through `include`. **Therefore:** keep `<title>{% block title %}Pulso{% endblock %}</title>` in `base.html` directly, and DELETE the `<title>` line from `_head.html` (login/setup set a literal `<title>` themselves). Adjust `_head.html`: remove its `<title>` line.

- [ ] **Step 3: Smoke test** — `pytest tests/test_ui.py -q` → all pass (screens render; classes are cosmetic). *(Commit happens at end of Task 3 — Task 2 without `accent_fg` would render-error.)*

---

### Task 3: `accent_fg` helper + template globals + session color wiring

**Files:**
- Modify: `app/templates_config.py`, `app/projects/router.py` (`switch_project`, `project_settings_update`), `app/auth/router.py` (`setup_submit` session color)
- Test: Create `tests/test_theme_helpers.py`

**Interfaces:**
- Produces: Jinja globals `accent_fg(color: str | None) -> str` (returns `"#0a0a0a"` or `"#ffffff"`) and `BRAND_PRESETS: list[str]`; session keys `current_project_color` (set on switch/settings-save/setup).

- [ ] **Step 1: Write failing test `tests/test_theme_helpers.py`**

```python
"""accent_fg: derived foreground for any user-picked accent color (trust-boundary check)."""
from app.templates_config import accent_fg


def test_accent_fg_dark_text_on_light_colors():
    assert accent_fg("#e8b94a") == "#0a0a0a"   # ochre
    assert accent_fg("#ffb084") == "#0a0a0a"   # peach
    assert accent_fg("#ffffff") == "#0a0a0a"


def test_accent_fg_light_text_on_dark_colors():
    assert accent_fg("#1a3a3a") == "#ffffff"   # teal
    assert accent_fg("#6366f1") == "#ffffff"   # indigo default
    assert accent_fg("#ff4d8b") == "#ffffff"   # brand pink


def test_accent_fg_tolerates_junk():
    assert accent_fg(None) == "#ffffff"        # default indigo → white
    assert accent_fg("") == "#ffffff"
    assert accent_fg("#fff") == "#0a0a0a"      # 3-digit form
    assert accent_fg("nonsense") == "#ffffff"  # unparseable → safe default
```

- [ ] **Step 2: Run** `pytest tests/test_theme_helpers.py -v` → FAIL (`ImportError: accent_fg`).

- [ ] **Step 3: Implement in `app/templates_config.py`** (append):

```python
BRAND_PRESETS = ["#6366f1", "#ff4d8b", "#1a3a3a", "#b8a4ed", "#ffb084", "#e8b94a", "#ff6b5a", "#a4d4c5"]


def accent_fg(color: str | None) -> str:
    """Foreground (ink/white) legible sobre el color de acento elegido libremente.

    Umbral perceptual 0.35 sobre luminancia relativa WCAG: reproduce las elecciones
    de texto por tarjeta del design template (blanco sobre teal/coral/pink/indigo,
    tinta sobre ochre/peach/lavender/mint). Valores no parseables → blanco (default indigo).
    """
    c = (color or "#6366f1").lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        r, g, b = (int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except (ValueError, IndexError):
        return "#ffffff"

    def _lin(x: float) -> float:
        return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4

    lum = 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)
    return "#0a0a0a" if lum > 0.35 else "#ffffff"


templates.env.globals["accent_fg"] = accent_fg
templates.env.globals["BRAND_PRESETS"] = BRAND_PRESETS
```

- [ ] **Step 4: Session color wiring.** In `app/projects/router.py::switch_project`, inside the `if project and not project.archived_at ...` branch, after the existing session sets add:

```python
            request.session["current_project_color"] = project.color or "#6366f1"
```

In `project_settings_update`, add `request: Request` as first param; after `await db.commit()` add:

```python
    if request.session.get("current_project_id") == str(project.id):
        request.session["current_project_color"] = project.color or "#6366f1"
```

In `app/auth/router.py::setup_submit`, next to the existing `request.session["current_project_name"] = project.name` add:

```python
    request.session["current_project_color"] = project.color or "#6366f1"
```

- [ ] **Step 5: Run** `pytest tests/test_theme_helpers.py tests/test_ui.py tests/test_access_and_setup.py -q` → PASS; `ruff check app/ tests/`; `python -m mypy app/` → clean.

- [ ] **Step 6: Commit (Tasks 2+3 together)**

```bash
git add app/templates/partials/_head.html app/templates/base.html app/templates_config.py app/projects/router.py app/auth/router.py tests/test_theme_helpers.py
git commit -m "feat(ui): design-token spine — shared head, dark mode, per-project accent"
```

---

### Task 4: Nav, footer, mobile drawer (base.html body)

**Files:**
- Modify: `app/templates/base.html` (replace `<nav>`; add footer before toast; add drawer; extend inline JS)

**Interfaces:**
- Consumes: `.p-navlink*`, `.p-menu*`, `.p-btn*`, `toggleTheme()`, `openModal/closeModal` (existing), session keys `current_project_name/slug/color`.
- Produces: the shell every screen renders inside; drawer element `#nav-drawer` (a `data-modal` overlay); nav link set.

- [ ] **Step 1: Replace `<nav>`** with (complete block):

```html
{% set NAV = [("/backlog","Backlog"),("/prioridad","Priority"),("/hilos","Threads"),("/incidentes","Incidents"),("/ideas","Ideas")] %}
<header class="sticky top-0 z-50 bg-surface-soft/95 backdrop-blur border-b border-hairline">
  <nav class="max-w-7xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between gap-4">
    <div class="flex items-center gap-6 min-w-0">
      <button class="md:hidden p-2 -ml-2 text-ink" aria-label="Abrir menú" onclick="openModal('nav-drawer')">
        <svg class="w-6 h-6" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" d="M4 7h16M4 12h16M4 17h16"/></svg>
      </button>
      <a href="/" class="flex items-center gap-2 shrink-0" aria-label="Pulso — inicio">
        <svg width="26" height="26" viewBox="0 0 48 48" fill="none" aria-hidden="true" class="text-ink">
          <path d="M4 25 H11 L17 13 L24 36 L29 25 H33" stroke="currentColor" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
          <circle cx="41" cy="25" r="4.5" fill="#ff4d8b"/>
        </svg>
        <span class="text-lg font-semibold tracking-tight text-ink">pulso</span>
      </a>
      <div class="hidden md:flex items-center gap-5">
        {% for href, label in NAV %}
        <a href="{{ href }}" class="p-navlink {% if request.url.path == href or request.url.path.startswith(href ~ '/') %}p-navlink-active{% endif %}">{{ label }}</a>
        {% endfor %}
      </div>
    </div>
    <div class="flex items-center gap-2 sm:gap-3">
      <details class="p-menu">
        <summary class="p-pill border border-hairline text-body hover:bg-surface-strong" aria-label="Proyecto actual">
          <span class="inline-block w-2.5 h-2.5 rounded-full" style="background:var(--accent)"></span>
          <span class="hidden sm:inline max-w-[10rem] truncate">{{ request.session.get("current_project_name") or "Select project" }}</span>
          <svg class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" d="M6 9l6 6 6-6"/></svg>
        </summary>
        <div class="p-menu-panel">
          <a href="/projects" class="p-menu-item">Cambiar proyecto…</a>
          {% if request.session.get("current_project_slug") %}
          <a href="/projects/{{ request.session['current_project_slug'] }}/settings" class="p-menu-item">Settings del proyecto</a>
          {% endif %}
          {% if user.account_role == 'owner' %}
          <a href="/projects/new" class="p-menu-item">＋ Nuevo proyecto</a>
          {% endif %}
        </div>
      </details>
      <button onclick="toggleTheme()" class="p-2 rounded-xl text-muted hover:text-ink hover:bg-surface-strong" aria-label="Cambiar tema claro/oscuro">
        <svg class="w-5 h-5 hidden dark:block" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path stroke-linecap="round" d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
        <svg class="w-5 h-5 dark:hidden" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/></svg>
      </button>
      <details class="p-menu">
        <summary class="w-9 h-9 rounded-full bg-surface-strong text-ink text-sm font-semibold flex items-center justify-center" aria-label="Menú de usuario">{{ (user.name or "?")[:1] | upper }}</summary>
        <div class="p-menu-panel">
          <div class="px-3 py-2 border-b border-hairline mb-1">
            <p class="text-sm font-medium text-ink truncate">{{ user.name }}</p>
            <p class="text-xs text-muted truncate">{{ user.email }}</p>
          </div>
          {% if user.account_role == 'owner' %}<a href="/account/members" class="p-menu-item">Members</a>{% endif %}
          {% if user.is_superadmin %}
          <a href="/admin/accounts" class="p-menu-item">Accounts</a>
          <a href="/admin" class="p-menu-item">Admin</a>
          {% endif %}
          <form method="post" action="/auth/logout"><button class="p-menu-item text-error">Log out</button></form>
        </div>
      </details>
    </div>
  </nav>
  <div class="h-[3px]" style="background:var(--accent)"></div>
</header>

{# drawer móvil: reutiliza el sistema de modales (Esc, backdrop, scroll-lock) #}
<div id="nav-drawer" data-modal class="hidden fixed inset-0 z-[90] bg-black/40 md:hidden">
  <div class="absolute inset-y-0 left-0 w-72 max-w-[85%] bg-surface-card border-r border-hairline p-5 flex flex-col gap-1 overflow-y-auto">
    <div class="flex items-center justify-between mb-3">
      <span class="text-lg font-semibold tracking-tight text-ink">pulso</span>
      <button onclick="closeModal('nav-drawer')" aria-label="Cerrar menú" class="p-2 text-muted">✕</button>
    </div>
    {% for href, label in NAV %}
    <a href="{{ href }}" class="p-menu-item text-base {% if request.url.path == href or request.url.path.startswith(href ~ '/') %}bg-surface-strong text-ink{% endif %}">{{ label }}</a>
    {% endfor %}
    <div class="border-t border-hairline mt-2 pt-2">
      <a href="/projects" class="p-menu-item">Proyectos</a>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Footer** — insert before the `#toast` div:

```html
<footer class="mt-16 border-t border-hairline bg-surface-soft">
  <div class="max-w-7xl mx-auto px-4 sm:px-6 py-8 flex flex-col sm:flex-row items-center justify-between gap-3 text-sm text-muted">
    <div class="flex items-center gap-2">
      <svg width="18" height="18" viewBox="0 0 48 48" fill="none" aria-hidden="true" class="text-ink"><path d="M4 25 H11 L17 13 L24 36 L29 25 H33" stroke="currentColor" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/><circle cx="41" cy="25" r="4.5" fill="#ff4d8b"/></svg>
      <span class="font-medium text-body">pulso</span>
      <span class="hidden sm:inline">· The backlog your agent reads and writes.</span>
    </div>
    <a href="mailto:rcuevas@tidanalytics.com" class="underline underline-offset-2" style="color:var(--accent)">¿Ayuda? rcuevas@tidanalytics.com</a>
  </div>
</footer>
```

- [ ] **Step 3: Close-details-on-outside-click** — append inside the existing modal-helpers `<script>` IIFE:

```javascript
      // Cierra los <details class="p-menu"> abiertos al hacer clic fuera.
      document.addEventListener("click", function (event) {
        document.querySelectorAll("details.p-menu[open]").forEach(function (d) {
          if (!d.contains(event.target)) d.removeAttribute("open");
        });
      });
```

- [ ] **Step 4: Run** `pytest tests/test_ui.py tests/test_routes_extra.py -q` → PASS.

- [ ] **Step 5: Commit** — `git add app/templates/base.html && git commit -m "feat(ui): new shell — nav with project/user menus, theme toggle, drawer, footer"`

---

### Task 5: Shared partials — status badge, priority select, hint macro, new-item modal

**Files:**
- Modify: `app/templates/partials/_status_badge.html`, `app/templates/partials/_priority_select.html`
- Create: `app/templates/partials/_hint.html`, `app/templates/partials/_new_item_modal.html`

**Interfaces:**
- Consumes: `.p-pill .p-input .p-label .p-btn*` (Task 2).
- Produces: `_status_badge.html` (usage unchanged: include with `item`), `_priority_select.html` (unchanged contract incl. optional `select_class`), macro `hint(id, text)` imported as `{% from "partials/_hint.html" import hint %}`, and `_new_item_modal.html` (expects `scopes` in context; modal id `new-item-modal`).

- [ ] **Step 1: Rewrite `_status_badge.html`** (complete file — THE canonical status map):

```html
{# Pill de estado — mapa canónico de colores (único lugar). Uso: include con `item`. #}
{% set S = {
  "idea":        "bg-brand-lavender/25 text-ink",
  "backlog":     "bg-surface-strong text-body",
  "spec":        "bg-brand-mint/30 text-ink",
  "in-progress": "bg-brand-ochre/30 text-ink",
  "blocked":     "bg-warning/20 text-warning",
  "in-review":   "bg-brand-peach/30 text-ink",
  "done":        "bg-success/15 text-success",
  "discarded":   "bg-surface-strong text-muted line-through",
} %}
<span class="p-pill {{ S.get(item.status, 'bg-surface-strong text-body') }}">{{ item.status }}</span>
```

- [ ] **Step 2: `_priority_select.html`** — keep its existing hx-post/hx-vals/options EXACTLY; only change the class attr to `class="p-input p-input-sm {{ select_class or '' }}"`.

- [ ] **Step 3: Write `_hint.html`** (complete file):

```html
{% macro hint(id, text) %}
<div id="hint-{{ id }}" class="hidden items-start gap-3 text-sm text-muted bg-surface-soft border border-hairline rounded-xl px-4 py-3 mb-4">
  <svg class="w-4 h-4 mt-0.5 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path stroke-linecap="round" d="M12 8h.01M11 12h1v4h1"/></svg>
  <p class="flex-1">{{ text }}</p>
  <button type="button" class="shrink-0 hover:text-ink" aria-label="Descartar ayuda"
          onclick="try{localStorage.setItem('hint-{{ id }}','1')}catch(e){};document.getElementById('hint-{{ id }}').remove()">✕</button>
</div>
<script>
  try { if (!localStorage.getItem("hint-{{ id }}")) {
    var h = document.getElementById("hint-{{ id }}"); h.classList.remove("hidden"); h.classList.add("flex");
  } } catch (e) {}
</script>
{% endmacro %}
```

- [ ] **Step 4: Write `_new_item_modal.html`** — move the existing `#new-item-modal` form out of `backlog.html` verbatim (same fields: `title`, `scope_id`, `type`, `summary_md`; same `action="/ui/items/create"`), reskinned: overlay `data-modal` + `fixed inset-0 z-[95] bg-black/40 hidden flex items-center justify-center p-4`, panel `p-card w-full max-w-lg p-6`, `p-label` labels, `p-input` fields, footer `p-btn p-btn-ghost` (cancel via `closeModal('new-item-modal')`) + `p-btn p-btn-primary` submit.

- [ ] **Step 5: Update `backlog.html`** minimally (full reskin comes in Task 8): replace its inline modal block with `{% include "partials/_new_item_modal.html" %}`.

- [ ] **Step 6: Run** `pytest tests/test_ui.py -q` → PASS. **Commit:** `git add app/templates/partials app/templates/backlog.html && git commit -m "feat(ui): token-styled shared partials — status map, hint macro, item modal"`

---

### Task 6: Login + Setup adopt the shared head

**Files:**
- Modify: `app/templates/login.html`, `app/templates/setup.html`

**Interfaces:**
- Consumes: `_head.html` (Task 2), `.p-card .p-btn .p-input .p-label`, `toggleTheme()`.
- Produces: nothing consumed later; standalone screens.

- [ ] **Step 1:** In both files replace their entire `<head>` inner content with `{% include "partials/_head.html" %}` plus a literal `<title>Pulso — Login</title>` / `<title>Pulso — Setup</title>` line, and add the accent style attr on `<html>` exactly as base.html (Task 2 Step 2).
- [ ] **Step 2:** Reskin the body: `bg-canvas` floor; centered `p-card w-full max-w-sm p-8` card; the brand lockup (same 26px svg + `pulso` wordmark from Task 4) centered above the form; `p-label`/`p-input` fields; `p-btn p-btn-primary w-full` submit; error banner → `bg-error/10 border border-error/30 text-error rounded-xl px-4 py-3 text-sm`. Add a fixed top-right theme toggle button (same as Task 4's, `class="fixed top-4 right-4 …"`). Setup: below the project-name field add tagline `<p class="text-xs text-muted">The backlog your agent reads and writes.</p>`.
- [ ] **Step 3:** Setup first-project color (spec §7): in `setup.html` add before submit:

```html
<label class="p-label">Color del proyecto</label>
<div class="flex items-center gap-2 flex-wrap mb-4">
  {% for c in BRAND_PRESETS %}
  <button type="button" class="w-7 h-7 rounded-full border border-hairline" style="background:{{ c }}"
          onclick="document.getElementById('setup-color').value='{{ c }}'" aria-label="Elegir {{ c }}"></button>
  {% endfor %}
  <input type="color" name="color" id="setup-color" value="#6366f1"
         class="w-9 h-9 rounded-xl border border-hairline cursor-pointer" aria-label="Color personalizado">
</div>
```

In `app/auth/router.py::setup_submit` add param `color: str = Form("")` and change the create call to `await create_project(db, name=project_name, account_id=acc.id, color=color or None)`.

- [ ] **Step 4: Run** `pytest tests/test_auth.py tests/test_access_and_setup.py -q` → PASS; ruff + mypy clean.
- [ ] **Step 5: Commit** — `git add app/templates/login.html app/templates/setup.html app/auth/router.py && git commit -m "feat(ui): login/setup on shared head — themed auth + first-project color"`

---

## Sprint 2 — Home + core screens

### Task 7: Home card launcher (router + template + tests)

**Files:**
- Modify: `app/ui/router.py::dashboard` (lines 54–104), `app/templates/dashboard.html` (full rewrite), `tests/test_ui.py:61` (assertion)
- Test: append `tests/test_ui.py::test_home_cards_stats`

**Interfaces:**
- Consumes: `.p-homecard`, brand color utilities, `_new_item_modal.html` (+ its `scopes` ctx need), `_status_badge.html`.
- Produces: ctx key `cards: dict` with keys `open`, `in_progress`, `blocked`, `quick_wins`, `threads_active`, `incidents_new`, `ideas` (all int); ctx `scopes` (for the modal); marker `id="home-cards"` used by tests.

- [ ] **Step 1: Failing test** (append to `tests/test_ui.py`):

```python
@pytest.mark.asyncio
async def test_home_cards_stats(client: AsyncClient):
    _uid, pid = await _login(client)
    await _seed_item(client, pid, title="Open A", status="backlog")
    await _seed_item(client, pid, title="Quick win", status="backlog", impact_ai=5, effort_ai="XS")
    await _seed_item(client, pid, title="An idea", status="idea")
    r = await client.get("/")
    assert r.status_code == 200
    assert 'id="home-cards"' in r.text
    assert "Backlog" in r.text and "Ideas" in r.text
```

Also change line 61's `assert r.status_code == 200 and "Tablero" in r.text` → `assert r.status_code == 200 and 'id="home-cards"' in r.text`.

- [ ] **Step 2: Run** `pytest tests/test_ui.py::test_home_cards_stats -v` → FAIL (`home-cards` not in output).

- [ ] **Step 3: Rewrite `dashboard()`** — replace the quick_wins list query with counts; add threads/incidents counts; add `scopes` for the modal:

```python
@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user_ui),
):
    from app.threads.models import Thread
    from app.webhooks.models import SentryIssue

    pid = await _project_id(db, user, request)
    counts_q = await db.execute(
        select(Item.status, func.count().label("n"))
        .where(Item.project_id == pid).group_by(Item.status)
    )
    counts = {row.status: row.n for row in counts_q}
    blocked_ids = await graph.graph_blocked_ids(db, project_id=pid)

    quick_wins_n = int(await db.scalar(
        select(func.count()).select_from(Item).where(
            Item.project_id == pid, Item.impact_ai >= 4,
            Item.effort_ai.in_(["XS", "S"]), Item.status.not_in(["done", "discarded"]),
        )
    ) or 0)
    threads_active = int(await db.scalar(
        select(func.count()).select_from(Thread).where(
            Thread.project_id == pid, Thread.stage.not_in(["hecho", "descartado"]),
        )
    ) or 0)
    incidents_new = int(await db.scalar(
        select(func.count()).select_from(SentryIssue).where(
            SentryIssue.project_id == pid, SentryIssue.status == "new",
        )
    ) or 0)

    recent_q = await db.execute(
        select(Item).where(Item.project_id == pid).order_by(Item.created_at.desc()).limit(10)
    )
    recent = recent_q.scalars().all()
    cost_q = await db.scalar(
        select(func.sum(AgentRun.cost_usd)).where(AgentRun.status == "ok", AgentRun.project_id == pid)
    )
    scopes = list((await db.execute(
        select(Scope).where(Scope.archived.is_(False), Scope.project_id == pid).order_by(Scope.name)
    )).scalars().all())

    cards = {
        "open": sum(counts.get(s, 0) for s in ("backlog", "spec", "in-progress", "blocked", "in-review")),
        "in_progress": counts.get("in-progress", 0),
        "blocked": len(blocked_ids),
        "quick_wins": quick_wins_n,
        "threads_active": threads_active,
        "incidents_new": incidents_new,
        "ideas": counts.get("idea", 0),
    }
    return templates.TemplateResponse(
        request, "dashboard.html",
        {
            "user": user, "cards": cards, "recent": recent,
            "recent_touch": {str(i.id): _recent_touch(i) for i in recent},
            "monthly_cost": float(cost_q or 0), "scopes": scopes,
        },
    )
```

- [ ] **Step 4: Rewrite `dashboard.html`** (complete structure):

```html
{% extends "base.html" %}
{% block title %}Pulso — Inicio{% endblock %}
{% block content %}
<div class="flex flex-wrap items-end justify-between gap-4 mb-8">
  <div>
    <h1 class="text-3xl md:text-4xl font-semibold tracking-tight text-ink">Hola, {{ (user.name or "").split(" ")[0] }}</h1>
    <p class="text-sm text-muted mt-2">
      {% if request.session.get("current_project_name") %}
      <span class="inline-block w-2.5 h-2.5 rounded-full align-middle mr-1" style="background:var(--accent)"></span>
      {{ request.session["current_project_name"] }} ·
      {% endif %}
      costo IA del mes: ${{ "%.2f"|format(monthly_cost) }}
    </p>
  </div>
  <button onclick="openModal('new-item-modal')" class="p-btn p-btn-primary">＋ Nuevo ítem</button>
</div>

<div id="home-cards" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
  <a href="/backlog" class="p-homecard bg-brand-teal text-white">
    <span class="w-9 h-9 rounded-xl bg-white/15 flex items-center justify-center">
      <svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" d="M4 6h16M4 12h16M4 18h10"/></svg>
    </span>
    <div>
      <p class="text-lg font-semibold">Backlog</p>
      <p class="text-sm opacity-80">{{ cards.open }} abiertos · {{ cards.in_progress }} en curso</p>
      {% if cards.blocked %}<p class="text-xs mt-1 text-brand-ochre">⛔ {{ cards.blocked }} bloqueados por dependencias</p>{% endif %}
    </div>
  </a>
  <a href="/prioridad" class="p-homecard bg-brand-ochre text-ink">
    <span class="w-9 h-9 rounded-xl bg-black/10 flex items-center justify-center">
      <svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" d="M3 17l6-6 4 4 8-8M14 7h7v7"/></svg>
    </span>
    <div><p class="text-lg font-semibold">Priority</p><p class="text-sm opacity-70">{{ cards.quick_wins }} quick wins</p></div>
  </a>
  <a href="/hilos" class="p-homecard bg-brand-lavender text-ink">
    <span class="w-9 h-9 rounded-xl bg-black/10 flex items-center justify-center">
      <svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01"/></svg>
    </span>
    <div><p class="text-lg font-semibold">Threads</p><p class="text-sm opacity-70">{{ cards.threads_active }} activos</p></div>
  </a>
  <a href="/incidentes" class="p-homecard bg-brand-coral text-white">
    <span class="w-9 h-9 rounded-xl bg-white/15 flex items-center justify-center">
      <svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" d="M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/></svg>
    </span>
    <div><p class="text-lg font-semibold">Incidents</p><p class="text-sm opacity-80">{{ cards.incidents_new }} nuevos</p></div>
  </a>
  <a href="/ideas" class="p-homecard bg-brand-peach text-ink">
    <span class="w-9 h-9 rounded-xl bg-black/10 flex items-center justify-center">
      <svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" d="M9 18h6M10 21h4M12 3a6 6 0 0 1 3.6 10.8c-.6.5-.6 1.2-.6 2.2h-6c0-1-.1-1.7-.6-2.2A6 6 0 0 1 12 3Z"/></svg>
    </span>
    <div><p class="text-lg font-semibold">Ideas</p><p class="text-sm opacity-70">{{ cards.ideas }}</p></div>
  </a>
</div>
{% if cards.open == 0 and cards.ideas == 0 %}
<p class="text-sm text-muted mt-4">Empieza aquí: crea tu primer ítem con «＋ Nuevo ítem» o conecta tu agente vía MCP en Settings del proyecto.</p>
{% endif %}

<section class="mt-10">
  <h2 class="text-lg font-semibold text-ink mb-3">Actividad reciente</h2>
  <div class="p-card divide-y divide-hairline">
    {% for item in recent %}
    <a href="/items/{{ item.id }}" class="flex items-center gap-3 px-4 py-3 hover:bg-surface-strong first:rounded-t-2xl last:rounded-b-2xl">
      {% if recent_touch.get(item.id|string) %}<span class="w-1.5 h-1.5 rounded-full shrink-0" style="background:var(--accent)" title="tocado en las últimas 24h"></span>{% endif %}
      <span class="flex-1 truncate text-sm text-ink">{{ item.title }}</span>
      {% include "partials/_status_badge.html" %}
      <span class="text-xs text-muted shrink-0">{{ item.created_at | fecha }}</span>
    </a>
    {% else %}
    <p class="px-4 py-6 text-sm text-muted">Sin actividad todavía.</p>
    {% endfor %}
  </div>
</section>
{% include "partials/_new_item_modal.html" %}
{% endblock %}
```

- [ ] **Step 5: Run** `pytest tests/test_ui.py -q` → PASS (incl. both changed assertions); ruff + mypy clean.
- [ ] **Step 6: Commit** — `git add app/ui/router.py app/templates/dashboard.html tests/test_ui.py && git commit -m "feat(ui): home card launcher with live per-module stats"`

---

### Task 8: Backlog + items_table reskin

**Files:**
- Modify: `app/templates/backlog.html`, `app/templates/partials/items_table.html`

**Interfaces:**
- Consumes: class map, `hint()` macro, `_status_badge`, `_priority_select`, `_new_item_modal`.
- Produces: nothing new — same ctx, same HTMX contract (`#filters` form, `#items-table` target, `hx-push-url`).

- [ ] **Step 1: `backlog.html`** — keep every `hx-*` attr and input name IDENTICAL. Apply:
  - Header: `<h1 class="text-2xl md:text-3xl font-semibold tracking-tight text-ink">Backlog</h1>` + count `<span class="text-sm text-muted">{{ items|length }} ítems</span>`; buttons → `p-btn p-btn-primary` (Nuevo ítem) / `p-btn p-btn-ghost` (+ Idea).
  - Blue banner → `{% from "partials/_hint.html" import hint %}` + `{{ hint('backlog', '<same text as today>') }}`.
  - Filter form `#filters`: wrap in `div class="sticky top-[67px] z-40 bg-canvas/95 backdrop-blur py-2 -mx-1 px-1"`; selects → `p-input p-input-sm`; checkboxes keep native `accent-[color:var(--accent)] w-4 h-4`; labels → `text-xs text-muted`. On mobile wrap the whole control row in `<details class="md:open:block"><summary class="md:hidden p-btn p-btn-ghost p-btn-sm mb-2">Filtros ▾</summary>…</details>` — plus `<style>@media(min-width:768px){#filters details>summary{display:none}#filters details{display:block}}</style>`-equivalent using the simpler pattern: `<details class="md:pointer-events-none" open>` is fragile — instead duplicate nothing: use `<details {% if false %}{% endif %}>` NO. **Do this:** `<details class="group"><summary class="md:hidden …">Filtros ▾</summary><div class="hidden group-open:flex md:!flex flex-wrap gap-2 items-center">…controls…</div></details>` (`md:!flex` forces visible on desktop regardless of open state).
  - `#items-table` container: `p-card overflow-hidden`.
- [ ] **Step 2: `items_table.html`** — each row `div class="flex flex-wrap md:flex-nowrap items-center gap-2 md:gap-3 px-4 py-3 border-b border-hairline last:border-0 hover:bg-surface-strong"`. Type chip: `<span class="p-pill {{ TYPE_TINT }}">{{ item.type }}</span>` using the canonical type map inline via a `{% set T = {...} %}` dict at top (copy the Global-Constraints map verbatim). Effort chip `p-pill bg-surface-strong text-muted`. Title `<a class="font-medium text-ink hover:underline">` keeping the ⛔/🔓/●/⚠ indicator logic byte-for-byte. Meta line `text-xs text-muted`. Right controls (priority select include + status move select) keep hx-attrs; classes → `p-input p-input-sm`. Empty state: `<div class="px-4 py-12 text-center text-sm text-muted">Sin ítems con estos filtros.</div>`.
- [ ] **Step 3: Run** `pytest tests/test_ui.py -q` (covers /backlog + HX partial + filters) → PASS.
- [ ] **Step 4: Commit** — `git add app/templates/backlog.html app/templates/partials/items_table.html && git commit -m "feat(ui): backlog reskin — sticky filter bar, token rows, mobile stacking"`

---

### Task 9: Priority matrix reskin

**Files:**
- Modify: `app/templates/prioridad.html`, `app/templates/partials/prioridad_body.html`

**Interfaces:**
- Consumes: class map, hint macro, `_status_badge`, `_priority_select`; existing ctx (`matrix`, `efforts`, `impacts`, `unestimated`, `items`).
- Produces: nothing new; HTMX contract unchanged (`#pf` form → `#prioridad-body`).

- [ ] **Step 1: `prioridad.html`** — header pattern as Task 8; scope select → `p-input p-input-sm` (keep hx-attrs); banner → `{{ hint('prioridad', '<same text>') }}`.
- [ ] **Step 2: `prioridad_body.html`** — matrix wrapper: `div class="overflow-x-auto snap-x"` with `<table class="w-full min-w-[560px] border-separate border-spacing-1">`. Header cells `text-xs uppercase tracking-wider text-muted`. Cells: `rounded-xl bg-surface-card border border-hairline p-1.5 align-top`; quick-win cells (impact ≥ 4 AND effort in XS/S) add `!bg-success/10 !border-success/30`. Item chips inside cells: `p0 → bg-brand-coral text-white`, `p1 → bg-brand-ochre text-ink`, else `bg-surface-strong text-body`, all `p-pill block truncate max-w-[9rem]` linking to the item. "Sin estimar" section → `p-card p-4 mt-6`; the enrich button → `p-btn p-btn-primary` KEEPING its `hx-post="/api/v1/items/enrich-pending"`, `hx-swap="none"` and self-label-update JS, and add the branded spinner inside the button: `<svg class="p-spin" width="18" height="18" viewBox="0 0 48 48" fill="none"><path d="M4 25 H11 L17 13 L24 36 L29 25 H33" stroke="currentColor" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/><circle cx="41" cy="25" r="4.5" fill="#ff4d8b"/></svg>` (htmx toggles it via `.htmx-request`). Ranked list below → `p-card divide-y divide-hairline` rows like Task 7's feed.
- [ ] **Step 3: Run** `pytest tests/test_ui.py tests/test_sprint2.py -q` → PASS. **Commit:** `git add app/templates/prioridad.html app/templates/partials/prioridad_body.html && git commit -m "feat(ui): priority matrix reskin — quick-win tint, scroll-snap mobile"`

---

### Task 10: Threads kanban reskin

**Files:**
- Modify: `app/templates/hilos.html`

**Interfaces:**
- Consumes: class map, hint macro; ctx (`by_stage`, `stages`, `counts`).
- Produces: stage→color map `STAGE_TINT` reused by Task 14 (copy, do not import): `{"idea":"bg-brand-lavender/25","investigacion":"bg-brand-mint/30","historias":"bg-brand-peach/30","spec":"bg-brand-ochre/30","en-desarrollo":"bg-brand-teal/15","review":"bg-brand-pink/20","hecho":"bg-success/15","descartado":"bg-surface-strong"}`.

- [ ] **Step 1:** Header per Task 8 pattern; `+ Nuevo hilo` → `p-btn p-btn-primary`; banner → `{{ hint('hilos', '<same text>') }}`. Board: `div class="flex gap-3 overflow-x-auto snap-x snap-mandatory pb-2"`; each column `div class="snap-start shrink-0 w-56 bg-surface-soft border border-hairline rounded-2xl p-2"`; column header `<div class="flex items-center justify-between px-2 py-1.5"><span class="p-pill {{ STAGE_TINT[stage] }} text-ink">{{ stage }}</span><span class="text-xs text-muted">{{ by_stage[stage]|length }}</span></div>`. Thread cards: `a class="block p-card p-3 mb-2 hover:bg-surface-strong border-l-2" style="border-left-color:var(--accent)"` with title `text-sm font-medium text-ink` + counts line `text-xs text-muted` («N artefactos · M ítems», from `counts[t.id|string]`). Empty column: `p-2 text-xs text-muted">vacío`.
- [ ] **Step 2:** New-thread modal → same treatment as `_new_item_modal` (overlay `data-modal`, `p-card` panel, `p-label`/`p-input`, `p-btn` footer) keeping form fields (`title`, `scope_name`, `summary`) and action `/ui/hilos/create` intact.
- [ ] **Step 3:** Run `pytest tests/test_ui.py -q` → PASS. **Commit:** `git add app/templates/hilos.html && git commit -m "feat(ui): threads kanban reskin — tinted stage lanes, snap scroll"`

---

### Task 11: Incidents reskin + backfill modal

**Files:**
- Modify: `app/templates/incidentes.html`

**Interfaces:**
- Consumes: class map, hint macro; ctx (`issues`, `counts`, `incluir_ignorados`); existing endpoints `/ui/incidentes/{id}/promote|ignore`, `/ui/incidentes/backfill`.
- Produces: modal id `backfill-modal`.

- [ ] **Step 1:** Header + stat pills: `<span class="p-pill bg-brand-coral/20 text-ink">{{ counts.new }} nuevos</span> <span class="p-pill bg-surface-strong text-body">{{ counts.linked }} linkeados</span> <span class="p-pill bg-surface-strong text-muted">{{ counts.ignored }} ignorados</span>`. Banner → `{{ hint('incidentes', '<same text>') }}`. Toggle-ignorados link keeps its href; style `text-sm underline underline-offset-2` + `style="color:var(--accent)"`.
- [ ] **Step 2: Backfill → modal.** Replace the hidden `#backfill-box` + inline `classList.toggle` JS with: owner button `onclick="openModal('backfill-modal')"` (`p-btn p-btn-ghost p-btn-sm`) and a `data-modal` overlay `id="backfill-modal"` containing the SAME form (fields `org`, `project`, `token`, `query`; same `hx-post="/ui/incidentes/backfill"`, `hx-target="#backfill-result"`, `hx-indicator`) in a `p-card max-w-md p-6` panel; `#backfill-result` div inside the panel; submit `p-btn p-btn-primary` with the branded `p-spin` svg (as Task 9 Step 2).
- [ ] **Step 3:** Issue rows → `p-card` list (`divide-y divide-hairline`): level chip (`error → bg-error/15 text-error`, `warning → bg-warning/15 text-warning`, else `bg-surface-strong text-muted`), title `font-medium text-ink`, meta `text-xs text-muted` (project · events · first/last seen `| fecha` · Sentry link with accent color), triage chips `p-pill bg-surface-strong text-muted`. Actions right: promote form (priority select `p-input p-input-sm` + `p-btn p-btn-sm p-btn-primary`, keep `hx-post` + `hx-swap="none"`) and Ignorar `p-btn p-btn-sm p-btn-ghost` (keep hx-attrs). Empty state per Task 8.
- [ ] **Step 4:** Run `pytest tests/test_sprint5.py tests/test_ui.py -q` → PASS. **Commit:** `git add app/templates/incidentes.html && git commit -m "feat(ui): incidents reskin — stat pills, card rows, backfill modal"`

---

### Task 12: Ideas reskin

**Files:**
- Modify: `app/templates/ideas.html`

- [ ] **Step 1:** Header pattern; banner → `{{ hint('ideas', '<same text>') }}`; capture form card → `p-card p-5` with `p-label`/`p-input` fields and `p-btn p-btn-primary` submit — KEEP `hx-post="/api/v1/items"` + its `hx-on` reset/reload handler verbatim. Ideas list → `p-card divide-y divide-hairline` rows (title link `text-ink font-medium hover:underline`, date `text-xs text-muted`); empty state per Task 8.
- [ ] **Step 2:** Run `pytest tests/test_ui.py -q` → PASS. **Commit:** `git add app/templates/ideas.html && git commit -m "feat(ui): ideas reskin"`

---

## Sprint 3 — Detail screens + success feedback + accent picker

### Task 13: Item detail — two-column layout

**Files:**
- Modify: `app/templates/item_detail.html`, `app/templates/partials/relationship_list.html`

**Interfaces:**
- Consumes: class map, `_status_badge`, `_priority_select`; ctx unchanged (`item`, `scope`, `scopes`, `transitions`, `all_targets`, `blockers`, `subgraph`).
- Produces: layout only. ALL hx-endpoints (`/ui/items/{id}/transition|field|close|reopen|relationships`, `/api/v1/items/{id}/enrich|comments`) keep exact attrs.

- [ ] **Step 1: Two-column shell:**

```html
<div class="grid grid-cols-1 lg:grid-cols-[1fr_290px] gap-6 items-start">
  <div class="space-y-6 min-w-0 order-2 lg:order-1">  {# main: summary/rationale/blockers/relations/comments/history #}
  ...
  </div>
  <aside class="lg:sticky lg:top-24 space-y-4 order-1 lg:order-2">  {# sidebar: edit controls + actions #}
  ...
  </aside>
</div>
```

Mobile: `order-1` puts the sidebar (controls) first, per spec.

- [ ] **Step 2: Sidebar** `p-card p-4 space-y-3`: each field as `p-label` + its EXISTING inline-edit select (`p-input p-input-sm w-full`, keep `hx-post` transition/field attrs + `hx-vals`); static meta rows (scope, origen, created/updated `| fecha`) as `text-xs text-muted`. Below, an actions `p-card p-4 space-y-2`: `✨ Analizar con IA` → `p-btn p-btn-ghost w-full` + `p-spin` svg keeping `hx-post`/`hx-indicator`/`hx-on` label swap; `Marcar hecho / Descartar` → `p-btn p-btn-primary w-full` (`onclick="openModal('close-modal')"`); or the Reabrir form (`p-btn p-btn-ghost w-full`, keep `confirm()`).
- [ ] **Step 3: Main column:** title block (h1 `text-2xl font-semibold tracking-tight text-ink` + status badge include + stale `p-pill bg-warning/20 text-warning`); summary `p-card p-5 prose-sm whitespace-pre-wrap text-body`; impact rationale `p-card p-5 text-sm text-body` with `p-label` heading; blockers → `bg-warning/10 border border-warning/30 text-warning rounded-xl px-4 py-3 text-sm` listing blocker links; `#relations-section` stays SAME id wrapping the include; comments: timeline rows `border-l-2 border-hairline pl-4` — decision comments (`c.kind == 'decision'`) get `style="border-left-color:var(--accent)"` + `p-pill bg-brand-lavender/25 text-ink">decision`; comment form `p-input` textarea + `p-btn p-btn-primary p-btn-sm` (keep hx + reload handler); history rows `text-xs text-muted`.
- [ ] **Step 4: Close modal** — reskin `#close-modal` as `data-modal` overlay + `p-card` panel (keep radios `status=done|discarded`, `reason`, `commit_sha`, action `/ui/items/{id}/close`).
- [ ] **Step 5: `relationship_list.html`** — arcs as rows `flex items-center gap-2 text-sm py-1.5` (relation label `p-pill bg-surface-strong text-muted`, target link `text-ink hover:underline`, delete button `text-error hover:bg-error/10 rounded p-1` keeping `hx-delete/hx-confirm/hx-target`); add-arc form → `p-input p-input-sm` fields + `p-btn p-btn-sm p-btn-primary` (keep `hx-post` + swap into `#relations-section`).
- [ ] **Step 6:** Run `pytest tests/test_ui.py tests/test_items.py -q` → PASS. **Commit:** `git add app/templates/item_detail.html app/templates/partials/relationship_list.html && git commit -m "feat(ui): item detail two-column — sticky edit sidebar, decision-log accents"`

---

### Task 14: Thread detail — stage stepper

**Files:**
- Modify: `app/templates/hilo_detail.html`, `app/templates/partials/elaborate_draft.html`

**Interfaces:**
- Consumes: class map; `STAGE_TINT` map (copy from Task 10); ctx (`thread`, `artifacts`, `linked`, `scope`, `next_stage`, `prev_stage`).
- Produces: layout only; endpoints (`/ui/hilos/{id}/elaborate|advance|stage`) keep exact attrs.

- [ ] **Step 1: Stepper** (under the h1):

```html
{% set STAGES = ["idea","investigacion","historias","spec","en-desarrollo","review","hecho"] %}
<ol class="flex items-center gap-1 overflow-x-auto py-3" aria-label="Etapas del hilo">
  {% for s in STAGES %}
  {% set done = STAGES.index(thread.stage) > loop.index0 if thread.stage in STAGES else false %}
  <li class="flex items-center gap-1 shrink-0">
    <span class="p-pill {% if s == thread.stage %}text-white{% elif done %}bg-success/15 text-success{% else %}bg-surface-strong text-muted{% endif %}"
          {% if s == thread.stage %}style="background:var(--accent);color:var(--accent-fg)"{% endif %}>{{ s }}</span>
    {% if not loop.last %}<span class="w-4 h-px bg-hairline"></span>{% endif %}
  </li>
  {% endfor %}
</ol>
```

(If `thread.stage == "descartado"`, show instead a single `p-pill bg-surface-strong text-muted line-through">descartado` pill.)

- [ ] **Step 2:** Main card `p-card p-5`: summary `text-body whitespace-pre-wrap`; stage-advance box → `border border-hairline rounded-xl p-4 bg-surface-soft` with `✨ Elaborar con IA` (`p-btn p-btn-ghost` + `p-spin` svg, keep hx), `Avanzar a {{ next_stage }}` (`p-btn p-btn-primary`, keep form), `◂ Volver` + `Descartar hilo` (`p-btn p-btn-ghost p-btn-sm`, keep forms/confirms); `#draft-area` unchanged id. Artifacts → `<details class="p-card">` per artifact with `<summary class="px-4 py-3 cursor-pointer text-sm font-medium text-ink hover:bg-surface-strong rounded-2xl">` + body `px-4 pb-4 text-sm text-body whitespace-pre-wrap`. Linked items → `p-card divide-y divide-hairline` rows with `_status_badge` include.
- [ ] **Step 3:** `elaborate_draft.html` — yellow box → `border border-warning/40 bg-warning/5 rounded-xl p-4`; textarea → `p-input`; buttons → `p-btn p-btn-primary p-btn-sm` / `p-btn p-btn-ghost p-btn-sm` (keep POST/clear JS).
- [ ] **Step 4:** Run `pytest tests/test_ui.py tests/test_sprint4.py -q` → PASS. **Commit:** `git add app/templates/hilo_detail.html app/templates/partials/elaborate_draft.html && git commit -m "feat(ui): thread detail — stage stepper, artifact accordion"`

---

### Task 15: Success feedback — flash, overlay, green toast, handler wiring

**Files:**
- Create: `app/ui/flash.py`
- Modify: `app/templates/base.html` (toast upgrade + flash render), `app/ui/router.py` (`ui_close`, `ui_advance_hilo`, `ui_set_hilo_stage`, `ui_promote_issue`, `ui_ignore_issue`, `ui_create_item`, `ui_create_hilo`), `app/projects/router.py` (`switch_project`, `project_settings_update`)
- Test: Create `tests/test_flash.py`

**Interfaces:**
- Consumes: keyframes `p-pop`/`p-draw` (Task 2), existing `showToast` infra.
- Produces: `flash_success(request, *, message="", title="", celebrate=False)` (importable from `app.ui.flash`); client `showToast(message, kind)` where kind ∈ `"error"|"success"`; overlay element `#celebrate`.

- [ ] **Step 1: Failing tests `tests/test_flash.py`** (uses `_login`/`_seed_item` — copy the two helpers from `tests/test_ui.py` imports: `from tests.test_ui import _login, _seed_item`):

```python
"""Session-flash: celebración pop-once en completar; toast verde en acciones."""
import pytest
from httpx import AsyncClient

from tests.test_ui import _login, _seed_item


@pytest.mark.asyncio
async def test_close_done_celebrates_once(client: AsyncClient):
    _uid, pid = await _login(client)
    item_id, _sid = await _seed_item(client, pid, title="Ship it", status="in-review")
    r = await client.post(f"/ui/items/{item_id}/close", data={"status": "done", "reason": "ok"})
    assert r.status_code == 204
    r1 = await client.get("/")
    assert "¡Completado!" in r1.text and "Ship it" in r1.text
    r2 = await client.get("/")          # pop-once: nunca se repite al refrescar
    assert "¡Completado!" not in r2.text


@pytest.mark.asyncio
async def test_close_discarded_toasts_not_celebrates(client: AsyncClient):
    _uid, pid = await _login(client)
    item_id, _sid = await _seed_item(client, pid, title="Nope", status="backlog")
    r = await client.post(f"/ui/items/{item_id}/close", data={"status": "discarded", "reason": "no"})
    assert r.status_code == 204
    r1 = await client.get("/")
    assert "¡Completado!" not in r1.text and "Ítem descartado" in r1.text
```

- [ ] **Step 2: Run** `pytest tests/test_flash.py -v` → FAIL.

- [ ] **Step 3: Write `app/ui/flash.py`**:

```python
from fastapi import Request


def flash_success(request: Request, *, message: str = "", title: str = "", celebrate: bool = False) -> None:
    """Señal de éxito de un solo uso; base.html la extrae (pop) y la pinta en el siguiente render.

    celebrate=True → overlay central «¡Completado!» (solo completar ítem/hilo).
    celebrate=False → toast verde inferior-derecha.
    """
    request.session["flash_success"] = {"message": message, "title": title, "celebrate": celebrate}
```

- [ ] **Step 4: base.html** — (a) toast div: remove `bg-red-600`, keep positioning classes; (b) upgrade `showToast`:

```javascript
      function showToast(message, kind) {
        if (!toast) return;
        toast.textContent = message;
        toast.classList.remove("hidden", "bg-error", "bg-success");
        toast.classList.add(kind === "success" ? "bg-success" : "bg-error");
        if (hideTimer) clearTimeout(hideTimer);
        hideTimer = setTimeout(function () { toast.classList.add("hidden"); }, kind === "success" ? 3000 : 5000);
      }
      window.showToast = showToast;
```

(existing error listeners keep calling `showToast(msg)` → kind undefined → error styling ✓). (c) Flash render — insert right after `<footer>`:

```html
{% set flash = request.session.pop("flash_success", None) %}
{% if flash %}
  {% if flash.celebrate %}
  <div id="celebrate" class="fixed inset-0 z-[110] flex items-center justify-center bg-black/40 p-4"
       role="status" aria-live="polite" onclick="this.remove()">
    <div class="p-card px-10 py-8 text-center max-w-sm" style="border-radius:24px;animation:p-pop .25s ease-out">
      <svg class="mx-auto mb-3" width="56" height="56" viewBox="0 0 52 52" fill="none" aria-hidden="true">
        <circle cx="26" cy="26" r="24" stroke="#22c55e" stroke-width="2" opacity=".25"/>
        <path d="M14 27 L22 35 L38 18" stroke="#22c55e" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"
              style="stroke-dasharray:40;stroke-dashoffset:40;animation:p-draw .4s .15s ease-out forwards"/>
      </svg>
      <p class="text-lg font-semibold text-ink">¡Completado!</p>
      {% if flash.title %}<p class="text-sm text-muted mt-1">«{{ flash.title }}»</p>{% endif %}
    </div>
  </div>
  <script>setTimeout(function () { var c = document.getElementById("celebrate"); if (c) c.remove(); }, 1600);</script>
  {% else %}
  <script>document.addEventListener("DOMContentLoaded", function () { showToast({{ flash.message | tojson }}, "success"); });</script>
  {% endif %}
{% endif %}
```

- [ ] **Step 5: Wire handlers** (`from app.ui.flash import flash_success`):
  - `ui_close`: add `request: Request` param; before `return _refresh()`: `flash_success(request, title=item.title, celebrate=True) if status == "done" else flash_success(request, message="Ítem descartado")` (as an if/else, not ternary-with-side-effects).
  - `ui_advance_hilo` + `ui_set_hilo_stage`: add `request: Request`; after commit: `if t.stage == "hecho": flash_success(request, title=t.title, celebrate=True)`.
  - `ui_promote_issue`: add `request: Request`; `flash_success(request, message="Incidente promovido al backlog")`.
  - `ui_ignore_issue`: add `request: Request`; `flash_success(request, message="Incidente ignorado")`.
  - `ui_create_item`: `flash_success(request, message="Ítem creado")` before the redirect.
  - `ui_create_hilo`: `flash_success(request, message="Hilo creado")`.
  - `switch_project` (projects/router.py): inside success branch `flash_success(request, message=f"Proyecto activo: {project.name}")`.
  - `project_settings_update`: `flash_success(request, message="Configuración guardada")` before redirect.
- [ ] **Step 6: Run** `pytest tests/test_flash.py tests/test_ui.py tests/test_sprint5.py -q` → PASS; ruff + mypy clean.
- [ ] **Step 7: Commit** — `git add app/ui/flash.py app/templates/base.html app/ui/router.py app/projects/router.py tests/test_flash.py && git commit -m "feat(ui): success feedback — pop-once flash, celebration overlay, green toast"`

---

### Task 16: Accent color picker — settings + new project

**Files:**
- Create: `app/templates/partials/_color_picker.html`
- Modify: `app/templates/projects_settings.html`, `app/templates/projects_new.html`

**Interfaces:**
- Consumes: `BRAND_PRESETS`, `accent_fg` globals (Task 3).
- Produces: macro `color_picker(current, label)` — renders swatches + `<input type="color" name="color">` + live preview pill. Field name stays `color` (handlers unchanged).

- [ ] **Step 1: Write `_color_picker.html`**:

```html
{% macro color_picker(current, label) %}
<div class="flex items-center gap-2 flex-wrap">
  {% for c in BRAND_PRESETS %}
  <button type="button" class="w-7 h-7 rounded-full border border-hairline hover:scale-110 transition-transform"
          style="background:{{ c }}" onclick="pkPick('{{ c }}')" aria-label="Elegir color {{ c }}"></button>
  {% endfor %}
  <input type="color" name="color" id="pk-input" value="{{ current or '#6366f1' }}"
         class="w-9 h-9 rounded-xl border border-hairline cursor-pointer" aria-label="Color personalizado"
         oninput="pkPreview(this.value)">
  <span id="pk-preview" class="p-pill ml-1" style="background:{{ current or '#6366f1' }};color:{{ accent_fg(current) }}">
    <span class="inline-block w-2 h-2 rounded-full bg-current opacity-70"></span>{{ label }}
  </span>
</div>
<script>
  function pkPick(c) { var i = document.getElementById("pk-input"); i.value = c; pkPreview(c); }
  function pkPreview(c) {
    var p = document.getElementById("pk-preview");
    p.style.background = c; p.style.color = pkFg(c);
  }
  function pkFg(c) {  /* espejo JS de accent_fg (umbral 0.35) */
    c = c.replace("#", "");
    var r = parseInt(c.substr(0, 2), 16) / 255, g = parseInt(c.substr(2, 2), 16) / 255, b = parseInt(c.substr(4, 2), 16) / 255;
    function L(x) { return x <= 0.04045 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); }
    return (0.2126 * L(r) + 0.7152 * L(g) + 0.0722 * L(b)) > 0.35 ? "#0a0a0a" : "#ffffff";
  }
</script>
{% endmacro %}
```

- [ ] **Step 2: `projects_settings.html`** — full reskin into sectioned cards: (1) breadcrumb `text-sm text-muted`; (2) **Connect Claude Code** card: `border rounded-2xl p-5` with `style="border-color:var(--accent);background:color-mix(in srgb, var(--accent) 8%, var(--canvas))"`, `new_token` one-time box (`font-mono text-sm select-all bg-canvas border border-hairline rounded-xl p-3`), snippet `<pre id="mcp-snippet" class="bg-canvas border border-hairline rounded-xl p-3 text-xs overflow-x-auto">` + copy button:

```html
<button type="button" class="p-btn p-btn-ghost p-btn-sm"
        onclick="var b=this;navigator.clipboard.writeText(document.getElementById('mcp-snippet').innerText).then(function(){b.textContent='Copiado ✓';setTimeout(function(){b.textContent='Copiar';},1500);})">Copiar</button>
```

(3) **API Tokens** card `p-card p-5`: generate form (`p-input` + `p-btn p-btn-primary p-btn-sm`), tokens table (`w-full text-sm`, th `p-label text-left pb-2`, td `py-2 border-t border-hairline`), Revoke → `p-btn p-btn-sm p-btn-ghost text-error` keep confirm; (4) **Project settings** owner card: replace bare `<input type="color">` with `{% from "partials/_color_picker.html" import color_picker %}` + `{{ color_picker(project.color, project.name) }}`; other fields `p-label`/`p-input`; secrets keep `<details>` (summary `text-sm text-muted cursor-pointer`); Save `p-btn p-btn-primary`.
- [ ] **Step 3: `projects_new.html`** — reskin form card (`p-card max-w-lg p-6`, `p-label`/`p-input`), replace color input with `{{ color_picker(None, 'Nuevo proyecto') }}`; buttons `p-btn p-btn-primary` / ghost cancel.
- [ ] **Step 4:** Run `pytest tests/test_routes_extra.py tests/test_ui.py -q` → PASS. **Commit:** `git add app/templates/partials/_color_picker.html app/templates/projects_settings.html app/templates/projects_new.html && git commit -m "feat(ui): project settings + accent picker with live preview and MCP copy button"`

---

## Sprint 4 — Quieter screens, sweep, gates

### Task 17: Projects list reskin

**Files:**
- Modify: `app/templates/projects_list.html`

- [ ] **Step 1:** `max-w-2xl` header (`Projects` + owner `p-btn p-btn-primary p-btn-sm` New project). Each project → `p-card p-4 flex items-center gap-3 border-l-4` with `style="border-left-color:{{ p.color or '#6366f1' }}"`; name `font-medium text-ink` + description `text-sm text-muted truncate`; archived pill `p-pill bg-surface-strong text-muted">archivado`; right side: **Active** → `p-pill" style="background:var(--accent);color:var(--accent-fg)">Activo` and the card additionally gets `ring-2 ring-[color:var(--accent)]`; else Switch form button `p-btn p-btn-ghost p-btn-sm` (same POST `/ui/project/switch` + hidden `project_id`); Settings link `text-sm underline underline-offset-2` accent-colored. Empty state per Task 8.
- [ ] **Step 2:** Run `pytest tests/test_ui.py -q` → PASS. **Commit:** `git add app/templates/projects_list.html && git commit -m "feat(ui): projects list — color stripes, active accent ring"`

---

### Task 18: Members, accounts admin, admin reskin

**Files:**
- Modify: `app/templates/account_members.html`, `app/templates/accounts_admin.html`, `app/templates/admin.html`, `app/templates/partials/token_created.html`

- [ ] **Step 1: `account_members.html`** — success/error banners → success/error token boxes (class map). Matrix: wrapper `p-card overflow-x-auto`; `<table class="w-full text-sm min-w-[640px]">`; first column cells `sticky left-0 bg-surface-card font-medium text-ink pr-4`; header row `p-label text-left pb-2`; grant selects keep `onchange="this.form.submit()"` + `p-input p-input-sm`. Add-collaborator form → `p-card p-5` with `p-label`/`p-input` + `p-btn p-btn-primary`.
- [ ] **Step 2: `accounts_admin.html`** — banners per class map; accounts table in `p-card overflow-x-auto` (same table pattern); enable/disable buttons → `p-btn p-btn-sm p-btn-ghost`; create-account form → `p-card p-5 grid sm:grid-cols-2 gap-4` with `p-label`/`p-input` + `p-btn p-btn-primary`.
- [ ] **Step 3: `admin.html`** — banner → hint macro `{{ hint('admin', '<same text>') }}`; each of the 4 sections → `p-card p-5` with `h2 class="text-lg font-semibold text-ink mb-3"` + table pattern (wrapper `overflow-x-auto`, th `p-label text-left pb-2`, td `py-2 border-t border-hairline text-sm text-body`); token form keeps `hx-post/hx-target="#token-result"/hx-indicator` with `p-input`/`p-btn p-btn-primary p-btn-sm` + `p-spin` svg.
  `token_created.html` → `bg-success/10 border border-success/30 rounded-xl p-4` panel; token `font-mono text-sm select-all text-ink`; the `claude mcp add` command in `<pre class="bg-canvas border border-hairline rounded-xl p-3 text-xs overflow-x-auto">`.
- [ ] **Step 4:** Run `pytest tests/test_routes_extra.py tests/test_isolation.py tests/test_ui.py -q` → PASS. **Commit:** `git add app/templates/account_members.html app/templates/accounts_admin.html app/templates/admin.html app/templates/partials/token_created.html && git commit -m "feat(ui): members/accounts/admin reskin — sticky matrix, token tables"`

---

### Task 19: Visual verification sweep (both themes, mobile)

**Files:** none created; fixes land in the templates they belong to.

- [ ] **Step 1:** Start the app locally (worker included): `TEST_DATABASE_URL` not needed — use dev DB per README/`.env`; `uvicorn app.main:app --port 8000`. Seed via `/setup` if fresh.
- [ ] **Step 2:** With the webapp-testing/Playwright toolkit, capture screenshots of EVERY screen (`/`, `/backlog`, `/prioridad`, `/hilos`, `/hilos/{id}`, `/incidentes`, `/ideas`, `/items/{id}`, `/projects`, `/projects/new`, `/projects/{slug}/settings`, `/account/members`, `/admin/accounts`, `/admin`, `/auth/login`, `/setup` on a fresh DB) at 1280px and 375px, in light AND dark (toggle via `localStorage.setItem('pulso-theme','dark')` + reload). Exercise: theme toggle persistence across navigation, project switch (accent + toast), item close → celebration overlay, drawer open/scroll-lock/Esc, dropdown outside-click close, hint dismiss persistence, copy button.
- [ ] **Step 3:** Fix every visual/behavioral defect found (contrast, overflow, spacing, dark-mode gaps). Re-shoot until clean. Checklist to verify explicitly: touch targets ≥44px on mobile controls; saturated home cards identical in both themes; no horizontal body scroll at 375px; focus ring visible on inputs in both themes.
- [ ] **Step 4: Commit** — `git add -A app/templates && git commit -m "fix(ui): visual sweep — dark/mobile polish across all screens"`

---

### Task 20: Full gates + PR

- [ ] **Step 1:** `TEST_DATABASE_URL="postgresql+asyncpg://efrain:efrain@localhost:5432/pulso_test" python -m pytest tests/ -q` → ALL pass (reset schema first if dirty). Coverage gate (90%) must hold — new Python lines are covered by `test_theme_helpers.py`, `test_flash.py`, `test_ui.py` additions.
- [ ] **Step 2:** `ruff check app/ tests/` → clean. `python -m mypy app/` → clean.
- [ ] **Step 3:** Update `CLAUDE.md` — UI bullet in the module table (`ui/`: add "design tokens in `partials/_head.html`, dark mode, per-project accent, flash success via `app/ui/flash.py`") and a line in Conventions ("UI: use token utilities / `.p-*` classes from `_head.html`; never hardcode grays; success feedback via `flash_success`").
- [ ] **Step 4:** Push + PR:

```bash
git push -u origin feat/ui-redesign
gh pr create --title "UI/UX redesign: token system, dark mode, card home, per-project accent, celebration feedback" --body "$(cat <<'EOF'
Implements docs/superpowers/specs/2026-07-03-pulso-ui-redesign-design.md

- Design-token spine (Tailwind CDN + CSS vars) — Clay-cream light / warm near-black dark, OS-default + remembering toggle
- New shell: nav (project & user menus, theme toggle, accent bar), footer with help email, mobile drawer
- Home: card launcher with live per-module stats (replaces dashboard)
- All 16 screens reskinned; item detail two-column; thread stage stepper; incidents backfill modal
- Success feedback: pop-once session flash → celebration overlay (item done / thread hecho) + green toast
- Per-project accent color: presets + free picker with live preview; luminance-derived foreground
- Brand kit wired: /static mount, favicons, manifest, branded spinner, inline theme-correct logo
- Tests: accent_fg, flash pop-once, home stats; full suite green

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5:** Watch CI (`gh pr checks --watch`). Fix any CI-only failures (pgvector image differences). Merge when green per repo convention. **Deploy is by tag after merge** (`git tag -a v2026.MM.DD-N && git push origin <tag>`), executed in-session after user-visible CI green.

---

## Self-review (done at plan time)

- **Spec coverage:** §3 tokens → T2/T3 · §4 shell → T2/T4 · §5 assets → T1/T2/T4 · §6 home → T7 · §7 screens → T5/T6/T8–T14/T16–T18 · §8 success → T15 · §9 responsive/a11y → per-task classes + T19 sweep · §11 file map → tasks 1:1 · §12 tests → T1/T3/T7/T15/T20. Gap check: setup color picker (spec §7) → T6 Step 3 ✓; `theme-color` per mode → T2 script ✓; OG/avatar images → **dropped deliberately** (marketing meta on an auth-walled app = YAGNI; avatars shipped in /static for future use — deviation noted).
- **Type consistency:** `flash_success(request, *, message, title, celebrate)` used identically in T15 wiring; `accent_fg` name matches T2's base.html usage and T16's JS mirror threshold (0.35 both sides); `cards` ctx keys match template; `p-*` class names cross-checked T2 ↔ T4–T18.
- **Placeholder scan:** the literal help-banner texts are referenced as "<same text as today>" — that is a *copy-preservation* instruction (text exists in the file being edited), not missing content. No TBDs remain.
- **Spec deviations (intentional):** greeting is static "Hola, {nombre}" (no time-of-day logic); admin tables scroll horizontally on mobile instead of stacking (superadmin desktop tool); OG images dropped; spec's accent_fg threshold "≈0.55" corrected to 0.35 (0.55 fails on ochre — verified against template card text choices).
