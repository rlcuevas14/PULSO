# CLAUDE.md — Pulso

> Gestor de backlog **agent-native** para Eduk3. El backlog vive como un grafo de ítems
> interconectados; una sesión de Claude Code lo lee y lo escribe vía **MCP**. Distinto del
> repo `efrain` (el SaaS escolar): Pulso es la capa que gestiona el backlog *de* efrain.

## Qué es

- **Producto**: backlog + grafo de dependencias + incidentes (Sentry) + hilos de desarrollo, todo accesible por MCP para que el agente que hace el trabajo mantenga el backlog solo.
- **Producción**: https://pulso.eduk3.cl · admin `rcuevas@tidanalytics.com` / `pulso2026`.
- **Repo**: `rlcuevas14/eduk3-pulso` (privado). **VM**: misma que efrain (161.153.193.32), stack en `/opt/pulso`.
- **MCP endpoint**: `https://pulso.eduk3.cl/mcp` (Streamable HTTP, modo JSON).

---

## Estado actual

**SPEC 100% implementada y EN PROD** (6 sprints). Último deploy por tag `v2026.06.13-7`; commit `010ce7f` (hardening senior, PR #1) mergeado — verificar que esté deployado antes de asumirlo.

- **Sprint 0** — gestión de backlog desde la UI: ciclo de 8 estados, apertura/cierre con motivo, priorización (matriz impacto×esfuerzo), edición inline.
- **Sprint 1** — enriquecimiento IA (Haiku impacto/esfuerzo + embeddings Gemini), mockeable, degrada sin API key.
- **Sprint 2** — grafo vivo: `item_relationships` (blocks/requires/conflicts/related/part_of), vecindad 2-hop, bloqueo derivado, orden topológico (Kahn).
- **Sprint 3** — MCP-over-HTTP (`/mcp`), **19 tools**, auth Bearer + scope read/write.
- **Sprint 4** — Hilos (funnel idea→…→hecho), artefactos, `elaborate-stage` IA, vínculo ítem↔hilo por `thread_id`.
- **Sprint 5** — webhooks Sentry/GitHub firmados (HMAC) + **Incidentes** (contenedor de errores de Sentry).

---

## Stack

FastAPI + SQLAlchemy async (asyncpg) + Alembic + **Jinja2 + HTMX 2 (CDN) + Tailwind (CDN)** — sin Node build. Postgres + pgvector. Worker asyncio in-proceso (cola en BD, `FOR UPDATE SKIP LOCKED`, sin Redis). **El MCP está hecho a mano** (JSON-RPC 2.0), NO con el SDK `mcp` — por control de auth/DB/testabilidad.

---

## Arquitectura (`app/`)

| Módulo | Responsabilidad |
|--------|-----------------|
| `main.py` | `create_app`, lifespan (arranca el worker), monta routers + `/mcp` (`mount_mcp`) |
| `config.py` | `Settings` (env vars) |
| `database.py` | engine, `SessionFactory`, `Base`, `get_db` |
| `templates_config.py` | `Jinja2Templates` + globals (`non_terminal_targets`, `allowed_targets`) + filtro `fecha` |
| `auth/` | `User`/`ApiToken`, bcrypt + tokens SHA-256, deps (cookie **o** Bearer), login UI |
| `items/` | `Item`/`ItemComment`/`ItemEvent`/`AiEnrichment`/`ItemRelationship`; `service.py` (mutaciones lifecycle-validadas); `lifecycle.py` (máquina 8 estados); `graph.py` (vecindad/bloqueo/Kahn); `relationships.py` (arcos); `importer.py` (JSONL) |
| `scopes/` | `Scope` (agrupador) + router |
| `threads/` | `Thread`/`ThreadArtifact` (stages), service, router |
| `webhooks/` | `SentryIssue`; service (firma HMAC, ingest, backfill, fetch stack trace, resolve); router (`/webhooks/sentry`, `/webhooks/github`) |
| `jobs/` | `AgentRun`; `worker.py` (poll-and-lease); `handlers.py` (`enrich`, `triage-sentry`) |
| `ai/` | `llm.py` — interfaz aislada/mockeable a Haiku (enrich/triage/generate_stage) + Gemini (embed). Degrada sin API key |
| `mcp/` | `server.py` (transporte JSON-RPC + registro de 19 tools + auth/scope); `tools.py` (implementaciones) |
| `ui/` | `router.py` — pantallas (`/`, `/backlog`, `/prioridad`, `/hilos`, `/incidentes`, `/items/{id}`, `/admin`) + endpoints `/ui/...` de acción (HTMX) |

---

## Modelo de datos (enums reales — NO inventar valores)

**`items`**: `id, scope_id, title, summary_md, type, status, priority, effort_ai, impact_ai, impact_rationale, effort_declared, priority_declared, trigger_text, dependencies, origen, source_refs(JSONB), stale_risk, agent_ready, created_by, created_at, updated_at, closed_at, last_touched_at, thread_id` + `embedding vector(768)` (solo BD, vacío sin F2) + `search_vector` (GENERATED, solo migración).

- **status**: `idea, backlog, spec, en-curso, bloqueado, en-revision, hecho, descartado`
- **type**: `bug, feature, tech-debt, infra, docs, ops, seguridad, producto, idea`
- **priority**: `p0..p3` · **effort_ai**: `XS..XL` · **impact_ai**: `1..5`
- **origen**: `digest, humano, ia-sesion, sentry, agente` (NO existe `claude-code`/`github`)
- **item_comments.kind**: `comentario, analisis-ia, decision, cambio-estado` (`decision` = decision log)

**Otras tablas**: `users, api_tokens, scopes, item_comments, item_events, ai_enrichments, sentry_issues, agent_runs, item_relationships, threads, thread_artifacts`.
`item_events(actor, action, payload)` es el **primitivo de auditoría** — toda mutación debe emitir uno.

**Migraciones** (head = `v0005`): v0001 (9 tablas) · v0002 (search_vector+GIN) · v0003 (item_relationships) · v0004 (last_touched_at + source_refs→JSONB) · v0005 (threads + items.thread_id).

---

## MCP — 19 tools

Config del cliente (Claude Code, NO Claude Desktop):
```bash
claude mcp add --transport http pulso https://pulso.eduk3.cl/mcp \
  --header "Authorization: Bearer <TOKEN>"
```
Token write se genera en `/admin` → "Generar token MCP". `protocolVersion 2025-03-26`. Auth Bearer obligatoria; las tools de escritura exigen scope `write` (si no → `isError`). **Las tools nuevas solo aparecen tras REINICIAR la sesión Claude Code** (don't-ask deniega tools no aprobadas — es client-side, no bug del server).

- **Lectura**: `pulso_contexto` (3 capas: local + vecindad-grafo + semántica), `pulso_buscar`, `pulso_listar`, `pulso_scopes`, `pulso_incidentes`, `pulso_incidente` (detalle CON stack trace de Sentry), `pulso_hilo_listar`, `pulso_hilo` (detalle)
- **Escritura**: `pulso_crear` (acepta `hilo_id`), `pulso_avanzar`, `pulso_completar`, `pulso_relacionar`, `pulso_mover_scope`, `pulso_incidente_resolver`, `pulso_hilo_crear`, `pulso_hilo_avanzar`, `pulso_hilo_vincular`
- **Prompts**: `briefing`, `decision`. **Resources**: `pulso://scope/{name}`, `pulso://graph/{item_id}`.

Los ítems devueltos incluyen `scope` (nombre) y `thread_id` cuando aplica. El grafo es **item↔item** (`part_of` es para epics entre ítems); la pertenencia a un hilo va por `thread_id`, no por el grafo.

---

## Conceptos clave

- **Ciclo de vida del ítem** (`lifecycle.py`): máquina de 8 estados con matriz de transiciones, validada en UI/REST/MCP. Terminales (`hecho`/`descartado`) van por `/close` (piden motivo).
- **Grafo vivo**: el bloqueo es **derivado** (un ítem está bloqueado si tiene un arco `blocks` entrante de un ítem abierto), no un estado materializado. `pulso_contexto` traversa la vecindad en tiempo real (anti context-collapse).
- **Incidentes (Sentry)**: el error aterriza en `sentry_issues` (**contenedor**, NO al backlog automático). Triage IA pre-clasifica el ruido; el owner/agente **promueve manualmente** los reales al backlog. Webhook firmado HMAC; `pulso:UUID` en commit auto-cierra (webhook GitHub).
- **Hilos**: funnel para features pesadas (el 80% va rápido por el backlog, sin hilos).
- **Append-only / auditoría**: cada mutación emite `ItemEvent`.

---

## Correr local + tests

**No hay pgvector local** (degrada con gracia — `embedding` es columna solo-migración). Postgres local en `localhost:5432` (`efrain`/`efrain`), base `pulso_test`:

```bash
TEST_DATABASE_URL="postgresql+asyncpg://efrain:efrain@localhost:5432/pulso_test" \
  python -m pytest tests/ -q          # 93 tests
ruff check app/ tests/                 # lint
python -m mypy app/                    # type check
```

**Gotcha de DB sucia**: la base `pulso_test` persiste entre corridas; `create_all` NO altera tablas existentes. Si cambias el schema o ves fallos que en CI no ocurren, **resetea**: `DROP SCHEMA public CASCADE; CREATE SCHEMA public;` y vuelve a correr (reproduce la DB limpia de CI).
**`search_vector`** (GENERATED, solo en migración) se parchea global en `conftest.py` para que el full-text funcione en todos los tests.
**CI es el gate real** (pgvector/pgvector:pg16). Push a `main` corre CI; deploy NO es automático.

---

## Deploy

```bash
git tag -a v2026.MM.DD-N -m "..." && git push origin v2026.MM.DD-N
```
Dispara `deploy.yml`: build multi-plataforma (amd64+arm64, la VM es ARM) → push a GHCR → SSH a `/opt/pulso` → `docker compose pull && up -d` → `alembic upgrade head`.

**Infra crítica (NO romper):**
- El contenedor `pulso-app-1` debe estar en la red `infra_frontnet` (en el `docker-compose.yml` de `/opt/pulso`) para que Caddy lo alcance por nombre.
- El bloque Caddy `pulso.eduk3.cl { reverse_proxy pulso-app-1:8000 }` vive **commiteado** en el repo efrain `infra/Caddyfile` (commit `f9c81f8`, marcado "NO BORRAR"). Si se borra, `/mcp` cae al wildcard `*.eduk3.cl` → Next.js de Eduk3 → 404. (Patrón `feedback_docker_recreate_wipes_patches`: parche en prod sin commitear = se pierde.)
- SSH key: `C:\Dev\efrain\ssh\ssh-key-2026-03-20.key`.

**Secretos en `/opt/pulso/.env`**: `DB_PASSWORD`, `SECRET_KEY`, `SENTRY_CLIENT_SECRET` (webhook), `SENTRY_API_TOKEN` + `SENTRY_ORG` (stack traces/resolver), `GITHUB_WEBHOOK_SECRET`, y opcionales `ANTHROPIC_API_KEY` (triage/enrich IA — hoy OFF para no gastar tokens) + `GEMINI_API_KEY` (embeddings).

---

## Convenciones

- **Cero voseo argentino** en TODO el copy (igual que efrain): tuteo neutro / español. Imperativos en tú ("crea", "elige", "configura"), nunca "creá"/"elegí"/"configurá".
- **Cada feature trae tests**; CI verde antes de tag.
- **LLM siempre vía `app/ai/llm.py`** (aislado y mockeable); degrada sin API key, nunca rompe el worker.
- **Trunk-based**: commit directo a `main` permitido; verificar local (ruff+mypy+pytest del área) antes de pushear; deploy solo por tag.
- Webhooks/escrituras externas: verificar firma HMAC, emitir `ItemEvent`, sanitizar contenido no confiable (XSS).

---

## Spin-off (futuro, diferido)

Existe la idea de abrir Pulso como **open-core** (core OSS self-host + cloud gestionado de pago: multi-tenant, SSO, backups), con posicionamiento "no markup de IA" (trae tu propia suscripción). **Decisión: NO construir infra de spin-off hasta validar demanda** — Pulso ya rinde como palanca interna de Eduk3. Documentar la visión en `docs/SPINOFF.md` cuando se retome; el primer hito barato sería **desacoplar Pulso de efrain** (docker-compose autocontenido, sin supuestos de la VM/Caddy) + licencia + docs.

---

## Cómo retomar

1. Lee este CLAUDE.md.
2. Estado en prod: https://pulso.eduk3.cl (`/backlog`, `/prioridad`, `/hilos`, `/incidentes`, `/admin`).
3. Tests locales contra `pulso_test` (resetea el schema si dudas de la DB sucia).
4. Cambios → CI verde → tag para deploy. No toques el bloque Caddy ni la red `infra_frontnet`.
