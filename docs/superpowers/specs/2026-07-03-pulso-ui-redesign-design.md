# PULSO — UI/UX Redesign Design Spec

**Date:** 2026-07-03
**Status:** Approved (brainstorming) → ready for implementation plan
**Author:** Rodolfo + Claude
**Scope:** Full visual + UX reform of the PULSO web app, based on `DESIGN-template.md` (Clay.com-inspired system) and the official Pulso brand kit in `assets/`.

---

## 1. Overview

Reform every PULSO screen onto a single design-token system: warm Clay-cream light theme + a warm near-black dark theme, a card-launcher home, per-project accent color, a completion celebration overlay, full responsiveness, and the official Pulso brand mark — **without adding a Node build**.

The mark is a **heartbeat/ECG waveform forming a "P", ending in a fixed hot-pink node `#ff4d8b`** — the same pink as the template's `brand-pink`, so logo and palette are one system. Positioning line: *"The backlog your agent reads and writes."*

### Goals
- One coherent visual language across all 16 screens (today: scattered inline Tailwind, inconsistent primary-button colors, no theme).
- Light + dark mode, OS-default, with a remembering top-right switch.
- Per-project accent color (subtle wayfinding tint), free-pick + theme presets.
- Card-launcher home, one saturated card per submodule carrying a live stat.
- Completion celebration overlay (visual only, no sound) on finishing a task.
- Mobile-responsive throughout.
- Wire the official brand kit (logo, spinner, favicons, avatars).

### Non-goals (explicit, out of scope)
- **No Node/PostCSS build.** Stays on the Tailwind CDN + inline config (the "CDN is dev-only" TODO in `base.html` is a *pre-existing* concern, unchanged by this work; documented as a known gap with an upgrade path).
- **No copy/route renaming.** Mixed ES/EN copy and Spanish routes (`/prioridad`, `/hilos`, `/incidentes`, `/ideas`) stay as-is. Thread stage names stay Spanish (out of scope per CLAUDE.md).
- **No drag-and-drop** on the threads kanban (stages advance from the detail screen, as today).
- **No service worker / offline PWA.** Favicons + web manifest only.
- **No changes to the HTMX interaction model** (inline edits keep their `HX-Refresh`/partial-swap patterns); we reskin and add feedback, not re-architect.

---

## 2. Locked decisions

| # | Decision | Choice |
|---|---|---|
| Default mode | Light/Dark/OS | **Follow OS on first load**; switch overrides & remembers (`localStorage`). |
| Home layout | cards vs dashboard | **Cards with live stats, replacing the dashboard**; slim recent-activity feed below. |
| Success feedback | overlay vs toast | **Center celebration overlay** for completions; **green toast** for other successful actions. |
| Success scope | which actions | **Completions only** (item → `done`, thread → `hecho`). |
| Delivery | how tokens ship | **Approach A**: Tailwind CDN + inline `tailwind.config` + CSS-variable `<style>` block. No build. |

---

## 3. Token spine (the foundation)

Lives once in a shared `_head.html` partial: an inline `tailwind.config` (before the CDN script) mapping semantic names to CSS variables, `darkMode: 'class'`, brand colors, font, radii; plus a `<style>` block declaring the palette as CSS variables for `:root` (light) and `html.dark` (dark).

### 3.1 Semantic color tokens

| Token (utility) | Light | Dark | Use |
|---|---|---|---|
| `canvas` | `#fffaf0` | `#0e0e10` | page floor |
| `surface-soft` | `#faf5e8` | `#17171a` | footer, sunken bands |
| `surface-card` | `#f5f0e0` | `#1c1c20` | cards, table rows |
| `surface-strong` | `#ebe6d6` | `#242429` | emphasized cards, hover |
| `ink` | `#0a0a0a` | `#f5f3ee` | headings, primary text |
| `body` | `#3a3a3a` | `#c9c7c1` | running text |
| `muted` | `#8a8578` | `#8f8d88` | meta, captions (warmed to brand kit) |
| `hairline` | `#eae5d9` | `#2a2a30` | 1px borders (warmed to brand kit) |
| `primary` / `on-primary` | `#0a0a0a` / `#ffffff` | `#f5f3ee` / `#0e0e10` | neutral CTA (inverts in dark) |
| `success` | `#22c55e` | `#22c55e` | success states, checkmark |
| `warning` | `#f59e0b` | `#f59e0b` | blockers, warnings |
| `error` | `#ef4444` | `#ef4444` | errors, red toast |

### 3.2 Brand card palette (identical in both themes)
Saturated fills for home cards, feature panels, status accents:
`pink #ff4d8b · teal #1a3a3a · lavender #b8a4ed · peach #ffb084 · ochre #e8b94a · coral #ff6b5a · mint #a4d4c5`.
Text-on-card: white on teal/coral/pink; ink on lavender/peach/ochre/mint.

### 3.3 Accent (`--accent`, per-project)
- Default `#6366f1` (indigo, matches existing default and brand-sheet link color).
- **Fixed vs per-project:** the **logo node is always `#ff4d8b`**; `--accent` is the **UI wayfinding tint** and varies per project. They are distinct roles.
- `--accent-fg` is **derived server-side from `--accent`'s relative luminance** via an `accent_fg(color)` helper in `templates_config.py` (WCAG-ish threshold ≈ 0.55 → `#0a0a0a` or `#ffffff`), so text on the accent is always legible regardless of the user's free pick. (Server-side, not an inline script, so it's unit-testable — see §12.)
- **Applied subtly, NOT on buttons:** active-nav underline/indicator, focus rings, links, project pill, the 3px accent bar under the nav, active-project card ring, priority-matrix quick-win emphasis. Buttons stay neutral (`primary`).
- **Set per request:** `<html class="..." style="--accent: {{ project.color }}; --accent-fg: {{ accent_fg }}">`. `accent_fg` computed server-side (or via a tiny inline script) from the color.

### 3.4 Typography — Inter only
- Loaded once from Google Fonts (`Inter:wght@400;500;600;700`).
- **Display = Inter 500–600, tight tracking** (the template's sanctioned Plain Black substitute; brand wordmark is Inter 600 / −0.025em).
- Scale (app-tightened from the marketing template): display `44/32/26/22` · title `20/17/15` · body `16/14` · label `12` uppercase +1.5px tracking · button `14/600`.

### 3.5 Radii / elevation / rhythm
- **Radii:** buttons+inputs `12` · content cards `16` · home/feature cards `24` · pills `9999`.
- **Elevation:** depth-from-color (template rule). Light: near-zero shadow. Dark: hairline border + one soft low-alpha shadow so cards lift off `#0e0e10`.
- **Rhythm:** 48–64px between major bands (app density; not the template's 96px).

---

## 4. Shell

### 4.1 Shared `_head.html`
Contains: meta + `theme-color`, `<title>` block, Google Fonts link, favicon/apple-touch `<link>`s + manifest, Tailwind CDN + inline `tailwind.config`, the token `<style>` block, and the **no-FOUC theme script**. Included by `base.html` **and** the standalone `login.html`/`setup.html` → single source of truth (fixes today's two-shell drift).

### 4.2 No-FOUC theme script (blocking, in `<head>`)
Runs before body paint: sets `html.dark` from `localStorage.theme ?? matchMedia('(prefers-color-scheme: dark)')`. The toggle flips the class + writes `localStorage`. First visit follows OS; switch overrides and remembers; no flash on the multi-page-app's full reloads. Also updates `<meta name="theme-color">`.

### 4.3 Top nav (`base.html`)
Sticky, `surface`-colored, 64px, a **3px `--accent` bar** on its bottom edge.
- **Left:** inline SVG mark (`stroke=currentColor` line + fixed `#ff4d8b` node) + lowercase `pulso` wordmark; then module links **Backlog · Priority · Threads · Incidents · Ideas**. Active link = ink + accent underline; others muted.
- **Right:** **project switcher dropdown** (color-dot pill + name → switch project / per-project Settings / Manage projects / owner: New project) — replaces today's plain link. Then **theme toggle** (moon/sun icon-button, `aria-label`). Then **user menu** (initials avatar → name/email + role-gated **Members / Accounts / Admin** *relocated here from the main bar* + Log out).
- **Mobile (<768):** module links collapse into a `☰` **drawer** (focus-trapped, reuses body-scroll-lock); theme toggle + avatar stay in the bar; project pill collapses to just the color dot.

### 4.4 Footer (`base.html`; minimal on login/setup)
`surface-soft` background (warm-light in light mode per template; dark-surface in dark), hairline top border. Left: mark + wordmark + tagline *"The backlog your agent reads and writes."* Right: `¿Ayuda? <maintainer-email>` as a `mailto:` in accent color.

---

## 5. Brand assets integration

- **Static mount (only new capability):** `app.mount("/static", StaticFiles(directory="app/static"))` — Starlette built-in, **no dependency**. Copy `assets/` (icons, `pulso-spinner.svg`, favicons, `png/` app-icons + avatars) → `app/static/`.
- **Favicon/PWA:** `<link>` favicon-16/32 + apple-touch-180 + `manifest.webmanifest` (name, icons from `png/`, `theme_color` = canvas). No service worker.
- **Nav logo:** inlined SVG (theme-correct line via `currentColor`, always-pink node). One markup, repaints via CSS on `html.dark` flip — no JS `src` swap, no flash.
- **Spinner:** every `hx-indicator` shows `pulso-spinner` with `@keyframes pulso-dash` (stroke-dasharray 61, 1.6s ease-in-out) + `pulso-blip` (node pulse), defined in the token `<style>`. Respects reduced-motion.
- **Avatars/OG:** `avatar-cream/dark` PNGs → `og:image` / twitter-card on login + home; user menu uses initials.

---

## 6. Home — card launcher (`GET /`, replaces dashboard)

Header: greeting (`Buen día, {{ user.name }}`), active-project context (color dot = accent) + AI monthly-cost figure (kept from old dashboard) + `＋ Nuevo ítem` quick-add (reskinned create modal).

**Card grid** — one saturated brand card per module (whole card is the link; no per-card "⋯" menu):

| Card | Fill / text | Live stat | Links to |
|---|---|---|---|
| Backlog | teal `#1a3a3a` / white | `N open` + `N in-progress` | `/backlog` |
| Priority | ochre `#e8b94a` / ink | `N quick wins` (impact≥4, effort XS/S) | `/prioridad` |
| Threads | lavender `#b8a4ed` / ink | `N active` threads | `/hilos` |
| Incidents | coral `#ff6b5a` / ink | `N new` (unpromoted Sentry) | `/incidentes` |
| Ideas | peach `#ffb084` / ink | `N` ideas | `/ideas` |

- Cards keep saturated fill in both themes (template rule); each has an icon chip top-left.
- **Recent activity** slim feed below (last ~10, type + status pill + title + relative time). It's a feed, not stat-counts — complements the cards.
- **Stats fetched in one grouped aggregate pass** per the home route (not five per-card queries) — keeps the landing fast as the backlog grows.
- **Empty project:** cards render `0`/`—`; Backlog card shows an "Empieza aquí" hint.
- **Responsive:** 3-up desktop → 2-up tablet → 1-up mobile (reduce columns, don't shrink cards).

---

## 7. Per-screen treatments

### Cross-cutting patterns
- **Blue help banner** (`bg-blue-50`, on 6 screens) → one reusable **collapsible hint** (muted `surface-soft` strip, info glyph, dismiss remembered in `localStorage`).
- **Status/priority badges** → token pills with **one canonical status→color map** (fixes per-screen inconsistency). `_status_badge.html` / `_priority_select.html` reskinned once, propagate everywhere.
- **Tables → responsive:** `<table>` on desktop, **stacked card-rows** below `md`.
- **Form kit:** inputs 44px / radius-12 / hairline border / **accent focus ring**; uppercase caption labels; neutral primary + ghost secondary buttons.
- **Modal kit:** `surface-card`, radius-16, backdrop blur, header/body/footer; reuse existing `openModal`/`closeModal`.

### Backlog `/backlog`
Display title + open-count; `＋ Nuevo ítem` (modal) + ghost `＋ Idea`. Filters → **sticky pill bar** (`category-tab` style), collapse to `Filtros ▾` on mobile. Rows: brand-tinted type chip, title + indicators (⛔🔓●⚠), status pill, inline priority/status selects (HTMX preserved), scope/impact meta; hover `surface-strong`. Friendly empty state. `partials/items_table.html` reskinned.

### Priority `/prioridad`
5×5 impact×effort matrix: token grid, quick-win quadrant tinted `success`/accent, item chips colored by priority (p0 coral · p1 ochre · p2/p3 muted). Ranked list reuses pills. `Enriquecer pendientes con IA` → primary + pulso-spinner. Mobile: list-first + matrix in a scroll-snap strip under a legend. `partials/prioridad_body.html` reskinned.

### Threads `/hilos`
Kanban lanes (Spanish stages) as `surface-soft` columns with colored stage header + count; thread cards = title + artifact/item counts + thin accent edge. Horizontal scroll-snap on mobile. `＋ Nuevo hilo` primary. No DnD.

### Incidents `/incidentes`
Counts → stat pills (new=coral, linked, ignored). Issue cards: level chip (error/warning token), title, meta (project · events · first/last · Sentry link), actions (promote-with-priority select · ignore). **Backfill → modal** (reuse modal system) instead of inline toggle. Promote/ignore success → **green toast**.

### Item detail `/items/{id}` — two-column
Main column (read): summary card (prose), impact rationale, dependencies, blockers (warning panel), relationships (graph arcs), comments (timeline; `kind=decision` gets accent border), history (timeline). Sticky sidebar (change): inline-edit selects (status/effort/impact/priority), scope/origin, created/updated; action buttons `✨ Analizar con IA` (pulso-spinner) + `Marcar hecho / Descartar` (close modal). Mobile: sidebar stacks first. All inline-edit HTMX preserved. **Marking `done` → celebration overlay.** Reopen keeps confirm.

### Thread detail `/hilos/{id}`
Header + **stage stepper** (8 Spanish stages, current highlighted). Stage-advance card (✨ Elaborar con IA · Avanzar a X · ◂ Volver · Descartar), reskinned draft editor, **artifacts accordion**, linked items as status chips. **Advancing to `hecho` → celebration overlay.**

### Projects list `/projects`
Project cards with **left accent stripe in the project color** + color dot, name/description, **Active** badge or `Switch` button, `Settings` link. Active project's card carries the accent ring.

### Project settings `/projects/{slug}/settings` (the configuraciones screen)
Sectioned cards: **Connect Claude Code** (accent callout; token shown once; `claude mcp add …` `<pre>` with a **copy button**); **API Tokens** (generate + table + Revoke-with-confirm); **Project settings** owner form (name/description/repo_url + the **color control**: preset swatches [brand palette + indigo] beside a free `<input type=color>` + **live accent-preview chip** rendering the actual nav pill; secrets in `<details>`).

### New project `/projects/new`
Same swatch+custom color control with live preview; default `#6366f1`.

### Members `/account/members` (owner)
Grant matrix reskinned: sticky member column, project columns, token `<select>` cells (native `onchange` auto-submit kept). Mobile: horizontal scroll-snap, member column pinned. Add-collaborator form. Success → green toast.

### Accounts admin `/admin/accounts` (superadmin)
Accounts table (enable/disable toggle) + create-account form, standard card kit.

### Admin `/admin` (superadmin)
Four data-table cards (users · service tokens · scopes · AI jobs); token creation keeps HTMX + reskinned `token_created.html` green success box.

### Login `/auth/login` & Setup `/setup` (standalone)
Adopt shared `_head.html` (inherit tokens/fonts/dark-mode). Centered card on `canvas`, brand mark on top, small corner theme toggle. Setup wizard adds the color-swatch picker for the first project. Tagline in footer strip.

---

## 8. Success feedback

Two distinct signals so "finished" ≠ "saved":

### 8.1 Celebration overlay — completions only
Dim backdrop + centered `surface-card` (radius-24), **`success`-green SVG checkmark** (stroke draws in ~400ms) + soft accent halo + optional CSS-only sparkle, message `¡Completado!` + the item/thread title. Auto-dismiss ~1.5s; click/Esc closes; **no sound**. `role="status" aria-live="polite"`.
- **Triggers (exactly two):** `item → done` (`/ui/items/{id}/close` with done) and `thread → hecho` (`/ui/hilos/{id}/advance` to `hecho`).
- `prefers-reduced-motion`: skip draw/sparkle → static ✓ + fade.

### 8.2 Green toast — every other successful manual action
Reuses the existing bottom-right toast, **green variant**, auto-hide ~3s, `role="alert"`. (Existing **red** error toast unchanged.) For: promote/ignore incident, save settings, token created, member added/granted, project switched, etc.

### 8.3 Mechanism — survives full-page reload
Completions/saves end in `HX-Refresh` or redirect, so the signal is carried by a **one-shot session flash**: the handler stashes `{kind, title, msg}` in `request.session`; `base.html` renders the overlay-or-toast on the next paint and **pops it (read-and-delete)** so a browser refresh never replays it. HTMX partial actions that don't reload may instead fire via an `HX-Trigger` header → the same client `showSuccess`/`showToast` helpers. One pair of client helpers, two feeders (session-flash for reloads, `HX-Trigger` for swaps).

---

## 9. Responsive & accessibility

- **Breakpoints:** mobile `<768` → drawer nav, cards 1-up, tables→card-rows, matrix/kanban scroll-snap, item-detail sidebar stacks first. Tablet `768–1024` → cards 2-up. Desktop `>1024` → full.
- **Touch targets** ≥ 44px (buttons/inputs spec'd at 44).
- **Contrast:** `--accent-fg` luminance-derived; brand-card text colors pre-chosen.
- **Motion:** `prefers-reduced-motion` disables celebration draw, spinner dash, hover-lift.
- **Keyboard/ARIA:** modals focus-trap + Esc (existing); drawer focus-trapped; overlay `role=status`, toasts `role=alert`; icon-only controls get `aria-label`.
- **`theme-color`** meta updates per mode.

---

## 10. Component inventory (reskinned/new)

- **Buttons:** primary (neutral ink/cream), secondary (hairline ghost), on-color (white over brand cards), text-link. All radius-12, 44px.
- **Inputs/selects:** 44px, radius-12, hairline, accent focus ring.
- **Cards:** content (radius-16), feature/home (radius-24, saturated), sidebar.
- **Badges/pills:** status pill (canonical map), priority pill, type chip, stat pill, category-tab pill.
- **Modals:** create-item, close-item, new-thread, backfill (new-as-modal).
- **Feedback:** celebration overlay (new), green toast (new variant), red toast (existing), pulso-spinner indicator.
- **Nav:** logo lockup, module links w/ active accent, project-switcher dropdown, theme toggle, user menu, mobile drawer.
- **Color control:** swatch presets + custom picker + live preview chip.
- **Collapsible hint** (replaces blue banners).
- **Stage stepper** (thread detail).

---

## 11. File change map

**New files**
- `app/templates/partials/_head.html` — shared head (tokens, config, fonts, favicon, no-FOUC script).
- `app/static/` — copied brand assets (`icons/`, `pulso-spinner.svg`, `favicons/`, `png/`, `manifest.webmanifest`).

(Celebration-overlay + green-toast markup and the `showSuccess`/`showToast` client helpers live **in `base.html`**, next to the existing toast/modal JS — not a separate partial. Keeps the feedback code with the code it extends.)

**Changed — templates**
- `base.html` — include `_head.html`; new nav (logo, project dropdown, theme toggle, user menu, accent bar); footer; drawer; render session-flash overlay/toast; keep modal/toast JS.
- `login.html`, `setup.html` — adopt `_head.html`; reskin; setup color picker.
- `dashboard.html` → card launcher home.
- `backlog.html`, `partials/items_table.html` — reskin, pill filters, responsive rows.
- `prioridad.html`, `partials/prioridad_body.html` — reskin matrix + list.
- `hilos.html`, `hilo_detail.html`, `partials/elaborate_draft.html` — reskin kanban, stage stepper, accordion.
- `incidentes.html` — reskin, backfill modal, stat pills.
- `item_detail.html`, `partials/relationship_list.html` — two-column, sticky sidebar.
- `ideas.html` — reskin.
- `projects_list.html`, `projects_new.html`, `projects_settings.html` — accent stripe/ring, color control + preview, copy button.
- `account_members.html`, `accounts_admin.html`, `admin.html` — reskin tables/forms.
- `partials/_status_badge.html`, `partials/_priority_select.html`, `partials/token_created.html` — reskin (propagate everywhere).

**Changed — Python**
- `app/main.py` — mount `/static` (StaticFiles).
- `app/ui/router.py` — home stats aggregate; set session-flash on item close / thread advance-to-hecho and on toast-worthy actions; pass `accent`/`accent_fg` + project color to templates (or via a context helper).
- `app/templates_config.py` — add globals/filters as needed (e.g. `accent_fg(color)` luminance helper, brand palette presets, status→color map).
- Handlers that should toast (incidents promote/ignore, settings save, members grant, project switch) — set the flash/`HX-Trigger`.

---

## 12. Testing strategy

Non-trivial logic gets one runnable check each (ponytail: smallest thing that fails if the logic breaks):
- **`accent_fg(color)` luminance** → `test_accent_fg.py`: asserts black on pale yellow `#e8b94a`/white, white on `#1a3a3a`/teal, and boundary near threshold.
- **Session-flash pop-once** → test: setting flash then rendering pops it; a second render shows nothing (no replay on refresh).
- **Home stats aggregate** → test: counts match seeded items (open/in-progress/quick-wins/active-threads/new-incidents/ideas).
- **Existing suite stays green** (CI is the gate); ruff + mypy clean. Visual reskin of templates is covered by existing route/render tests not breaking.

---

## 13. Known gaps / upgrade paths
- **Tailwind CDN in prod** — unchanged pre-existing TODO. Upgrade path = Approach B (real Tailwind build → static CSS). Out of scope here.
- **Copy/routes stay mixed ES/EN**; thread stages stay Spanish. Separate normalization effort if wanted.
- **No offline/service worker.** Favicons + manifest only.
- **No DnD** on kanban.
- Google Fonts + Tailwind CDN are external runtime deps (as today). Self-hosting fonts is a later option.

---

## 14. Suggested implementation sequencing (for the plan)
1. **Token spine + shared head + static mount** (foundation; nothing renders right without it).
2. **Shell** (nav, theme toggle, footer, drawer) + reskinned shared partials (badges/selects).
3. **Home card launcher** + stats aggregate.
4. **Core screens** (backlog, priority, threads, incidents) + detail screens (item, thread).
5. **Success feedback** (overlay + toast + session-flash) wired at the two completion points + toast actions.
6. **Per-project accent control** (settings/new/setup color picker + preview + `accent_fg`).
7. **Quieter screens** (projects, members, accounts, admin, login, setup).
8. **Responsive + a11y pass** across all.
9. Tests + ruff + mypy; CI green.
