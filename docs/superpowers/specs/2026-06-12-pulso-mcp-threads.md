# Pulso F2–F6: Gestión de backlog desde la app + Grafo vivo + MCP + Hilos + Sentry

**Goal:** Convertir Pulso en (1) la herramienta donde el owner gestiona el backlog desde el navegador — abrir, cerrar y priorizar tareas con visibilidad total — y (2) el conducto bidireccional entre sesiones de Claude Code y ese backlog, modelado como un grafo vivo de ítems interconectados.

**Arquitectura:** Sobre el Pulso ya en producción (FastAPI + Jinja2 + HTMX + Tailwind + Postgres + pgvector). Se agrega: UI mutable de backlog (Sprint 0), enriquecimiento IA (Sprint 1), grafo explícito `item_relationships` con traversal en SQL (Sprint 2), endpoint `/mcp` MCP Streamable HTTP vía el SDK oficial (Sprint 3), Hilos para features pesadas (Sprint 4), y webhooks Sentry/GitHub firmados (Sprint 5).

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic + HTMX 2 + Tailwind CDN; SDK `mcp>=1.0` (`FastMCP`, montado en la app existente); pgvector ya instalado; traversal de grafo en SQL puro (sin Neo4j, sin LangGraph).

**Principio rector:** el grafo y la priorización se leen siempre en vivo. No hay snapshot periódico ni artefacto de "visión global" — `pulso_contexto()` y la UI consultan el estado real en el momento.

> **Esta es la v2 del spec, reescrita tras una auditoría multi-agente (8 dimensiones, verificación adversarial).** Se corrigieron: una CTE recursiva ilegal en Postgres, la config MCP que apuntaba a Claude Desktop en vez de Claude Code, tres violaciones del esquema real (`origen='claude-code'`, `type=decisión`, `decision_log`), webhooks sin verificación de firma, y la secuencia (la PRIORIDAD 1 del owner —gestión desde la app— estaba ausente). Cada decisión cita la dimensión que la motivó.

---

## Contexto y motivación

Hoy cada sesión de Claude Code empieza en frío leyendo `BACKLOG.md` y termina sin rastro estructurado. Y el owner no tiene dónde **priorizar ni cerrar tareas con visibilidad** — la UI actual de Pulso es de solo lectura. Pulso debe ser, antes que nada, **la extensión visible y editable de BACKLOG.md**; y después, el conducto para los agentes.

El problema secundario es el **context collapse**: el agente optimiza la feature del día sin ver tensiones globales (el ítem en otro scope que depende del mismo módulo). Se resuelve con un grafo explícito vivo, no con resúmenes periódicos.

---

## Estado real del esquema (NO reinventar)

Verificado contra `migrations/versions/v0001_initial_schema.py`, `v0002_search_tsvector.py`, `app/items/models.py`. **El esquema ya tiene mucho de lo que un spec ingenuo crearía de cero.** Úsalo:

**Tabla `items`** (columnas reales): `id, scope_id, title, summary_md, type, status, priority, effort_ai, impact_ai, impact_rationale, effort_declared, priority_declared, trigger_text, dependencies, origen, source_refs, stale_risk, agent_ready, created_by, created_at, updated_at, closed_at`. Más `embedding vector(768)` **solo en la BD** (no en el ORM; vacío hasta Sprint 1) y `search_vector tsvector` GENERATED + índice GIN.

**Enums reales (CHECK constraints):**
- `status`: `idea, backlog, spec, en-curso, bloqueado, en-revision, hecho, descartado` (8 estados — ya incluye `spec` y `en-revision`).
- `type`: `bug, feature, tech-debt, infra, docs, ops, seguridad, producto, idea` (**no existe `decisión`**).
- `priority`: `NULL | p0 | p1 | p2 | p3`.
- `effort_ai`: `NULL | XS | S | M | L | XL`.
- `origen`: `digest, humano, ia-sesion, sentry, agente` (**no existe `claude-code` ni `github`**).
- `item_comments.kind`: `comentario, analisis-ia, decision, cambio-estado` (**`decision` ya existe → es el decision log**).

**Tablas existentes:** `users, api_tokens, scopes, items, item_comments, item_events, ai_enrichments, sentry_issues, agent_runs`.
- `item_events(actor, action, payload JSON)` — **primitivo de auditoría**. `PATCH /items/{id}` ya emite `status_changed`; `POST /items/{id}/close` ya emite `closed`. Toda mutación nueva debe emitir su `ItemEvent`.
- `sentry_issues(sentry_issue_id UNIQUE, project, title, level, triage, status, events_count, payload, first_seen, last_seen, item_id FK nullable)` — **ya diseñada para el flujo de Sentry**. La Parte de webhooks DEBE usarla, no crear ítems en paralelo.

**Endpoints existentes** (`app/items/router.py`): `GET/POST /api/v1/items`, `POST /import/digest`, `GET /search` (full-text `plainto_tsquery('spanish')` + `ts_rank`, **sin tie-break**), `GET/PATCH /api/v1/items/{id}` (PATCH emite ItemEvent si cambia status), `POST /{id}/comments`, `POST /{id}/close` (acepta `reason`), `POST /{id}/enrich` (encola job).

**Auth:** `ApiToken` con `token_hash` = SHA-256 sin salt; header `Authorization: Bearer`; deps `api_token_auth` / `api_or_session_user`. `ApiToken.scopes` es **un string** `read|write` (no lista) y **hoy no se valida en ningún endpoint**.

**Migración head real: `v0002`.** UUIDs: patrón dual — `server_default=gen_random_uuid()` en DDL (PG13+, ya asumido por v0001) **y** `default=uuid.uuid4` en el ORM. Los modelos nuevos deben seguir ambos.

**Tests:** conftest usa `Base.metadata.create_all` (NO migraciones). Toda tabla/columna nueva debe estar en el ORM o no existirá en los tests (lección: `search_vector` se parchea a mano en `test_search.py`).

---

## Secuencia de sprints (reemplaza el checklist monolítico)

> Motivado por las dimensiones *completitud/secuencia* y *UX*. La PRIORIDAD 1 del owner (gestión desde la app) va primero; los agentes/grafo/webhooks vienen después. F2-enriquecimiento se adelanta porque quickwins y la capa semántica nacen vacías sin él.

| Sprint | Entrega | Por qué este orden |
|--------|---------|--------------------|
| **0** | **Gestión de backlog desde la app**: ciclo de vida de 8 estados, apertura/cierre desde UI, priorización manual (priority + matriz). | PRIORIDAD 1. Cero dependencia de IA/MCP. Usa endpoints que ya existen. |
| **1** | **F2 enriquecimiento IA**: llenar `impact_ai`/`effort_ai`/`embedding` (Haiku + embeddings Gemini). | Prerequisito duro de quickwins y de la capa semántica del grafo. |
| **2** | **Grafo vivo**: `item_relationships`, traversal SQL, topological sort, reconciliación con `dependencies`. | Resuelve la preocupación de visión global. Necesita embeddings (S1) para la capa semántica. |
| **3** | **MCP-over-HTTP**: `/mcp` con SDK oficial, tools, prompts, auth por scope. | El conducto para Claude Code. Útil pero no en el camino crítico de PRIORIDAD 1. |
| **4** | **Hilos**: funnel para el 20% pesado. | Explícitamente "no debe estorbar el flujo rápido". |
| **5** | **Webhooks** Sentry + GitHub (firmados). | Automatización; al final. |

Cada sprint es deployable por sí solo. Sprint 0 es gate de los demás.

---

# Sprint 0 — Gestión de backlog desde la app (PRIORIDAD 1)

## 0.1 Ciclo de vida del ítem (máquina de estados de 8 estados)

> Hoy `PATCH /items` acepta cualquier `status` del CHECK sin validar transiciones. Se centraliza una **única función validadora** server-side (`items/lifecycle.py: valid_transition(from, to) -> bool`) consumida por UI, REST y MCP.

```
idea ──▶ backlog ──▶ spec ──▶ en-curso ──▶ en-revision ──▶ hecho
  │         │  │        │         │  ▲            │
  │         │  └────────┼─────────┘  │            │ (rework)
  │         │           │            ▼            ▼
  │         │           └──────▶ bloqueado ──▶ en-curso
  └─────────┴───────────┴────────────┴────────────┴────────▶ descartado (desde cualquiera)
hecho/descartado ──▶ backlog  (solo "Reabrir")
```

**Matriz de transiciones válidas** (origen → destinos permitidos):

| Desde | Destinos permitidos |
|-------|---------------------|
| `idea` | backlog, spec, en-curso, descartado |
| `backlog` | spec, en-curso, bloqueado, **hecho***, descartado |
| `spec` | backlog, en-curso, bloqueado, descartado |
| `en-curso` | backlog, bloqueado, en-revision, **hecho***, descartado |
| `bloqueado` | backlog, en-curso, descartado |
| `en-revision` | en-curso, bloqueado, **hecho***, descartado |
| `hecho` | backlog *(Reabrir)* |
| `descartado` | backlog *(Reabrir)* |

`*` Las transiciones a `hecho`/`descartado` NO pasan por el `PATCH` directo: van por el modal de cierre que pide motivo (→ `POST /{id}/close`).

**Dos verbos de apertura** (el spec previo solo tenía cierre):
1. **Crear ítem** — alta nueva (`POST /api/v1/items`, status inicial `backlog`).
2. **Avanzar** — `backlog→en-curso` ("empiezo a trabajar"), `en-revision→hecho`, etc. (`PATCH` validado). El simétrico de "completar".

## 0.2 UX/UI — diseño concreto

> El spec previo solo mencionaba `/hilos`. Toda la gestión de backlog (PRIORIDAD 1) faltaba. Diseño anclado en los idiomas ya presentes (`base.html`, `backlog.html`, `item_detail.html`): contenedor `max-w-7xl`, tarjetas `bg-white rounded-lg border`, badges de status por color, bloque de ayuda `bg-blue-50`, partials HTMX con `hx-get/hx-post + hx-target + hx-include`. **Copy tuteo neutro, cero voseo.**

### Inventario de pantallas

| Pantalla | Estado | Propósito |
|----------|--------|-----------|
| `/` Tablero | mejorada | Conteos sobre los 8 estados + tarjeta "Bloqueados por grafo" + quickwins + actividad con "recién tocado". |
| `/backlog` | mejorada | Núcleo: filtros (+origen, +tipo, +bloqueados), órdenes (`prioridad`, `topológico`), edición inline de status/priority, creación rápida, badges de grafo/origen/frescura. |
| `/items/{id}` | mejorada | Apertura/cierre: selector de transición, edición inline impact/effort/priority, sección **Relaciones**, modal cerrar/descartar, botón Reabrir. |
| `/prioridad` | **nueva** | Matriz impacto×esfuerzo (quadrant) + lista priorizada editable. |
| `/hilos`, `/hilos/{id}` | nueva (S4) | Kanban por stage + detalle con artifacts. |
| `/admin` | mejorada | Acción "Generar token MCP" con snippet copiable (S3). |

Nav (`base.html`): `Tablero · Backlog · Prioridad · Hilos · Ideas · Admin`.

### `/items/{id}` — apertura/cierre (CORE)

```
← Backlog
┌──────────────────────────────────────────────────────────────────────────┐
│ Migrar auth a JWT refresh                          ⚠Verificar  [bloqueado▾]│ ← status = selector
│ Tipo: feature  Scope: auth  Esfuerzo:[S▾] Impacto:[5▾] Prioridad:[p1▾]     │   de transiciones VÁLIDAS
│ ·origen: ia-sesion                                                          │
│ <summary_md …>                                                              │
│ ┌─ Acciones ──────────────────────────────────────────────────────────┐   │
│ │ [Analizar con IA]  [Marcar hecho ▸]  [Descartar ▸]                   │   │
│ │ (en estados terminales:)  [Reabrir ▸]                                │   │
│ │ ⛔ Bloqueado por: [GIN index], [Rotar keys]  ← feedback inline       │   │
│ └──────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
```

- **Selector de status**: `<select>` que lista solo destinos válidos desde el estado actual (matriz 0.1); inválidos ocultos o `disabled` con `title` explicativo. `PATCH /api/v1/items/{id}` → re-render del badge (HTMX, sin recargar). Emite `ItemEvent`.
- **Modal de cierre** (Marcar hecho / Descartar): pide `hecho|descartado` + motivo libre + `commit_sha` opcional → `POST /api/v1/items/{id}/close` (motivo real, no hardcodeado). `commit_sha` va a `source_refs`.
- **Reabrir**: visible solo en `hecho/descartado` → `PATCH` a `backlog` con confirm.
- **Edición inline** de `effort_ai/impact_ai/priority` como `<select>` → `PATCH`. Cuando el humano cambia `priority`, se setea `priority_declared` (regla: **lo declarado por el humano gana al juicio IA** en orden y matriz).
- **Feedback inline (DoD)**: si tiene bloqueadores abiertos, aviso ámbar bajo Acciones + `title` en "Marcar hecho". No se prohíbe cerrar (el humano manda), se avisa.
- **Ayuda contextual (DoD)**: bloque azul con cómo usar el selector y el cierre con motivo.

### `/backlog` — priorización inline

```
Backlog                                              [+ Nuevo ítem]  [+ Idea]
[Scope▾][Estado▾][Tipo▾][Origen▾][Orden: prioridad▾]  ☐ ⚠stale  ☐ ⛔bloqueados
                              └ reciente / impacto / prioridad / topológico
┌─────────────────────────────────────────────────────────────────────────┐
│ feature ⛔2  Migrar auth a JWT refresh  ●        [p1▾][bloqueado▾] I5 S auth│
│ tech-debt🔓 Crear índice GIN           ·ia-sesion[p2▾][en-curso ▾] I3 S auth│
│  (orden topológico: GIN index va ANTES porque bloquea a JWT refresh)       │
└─────────────────────────────────────────────────────────────────────────┘
  (vacío) → "No hay ítems con esos filtros. Crea uno con + Nuevo ítem."
```

- **Filtros nuevos** (patrón HTMX existente): Tipo (8), Origen (5 reales), checkbox `⛔bloqueados` (= `graph_blocked=1`).
- **Orden** amplía el `<select>`: `reciente · impacto · prioridad · topológico`. `prioridad` = p0→p3 luego impacto (funciona sin F2). `topológico` corre Kahn (S2).
- **Edición inline de status/priority en la fila** vía `partials/item_row_inline.html`. Destinos terminales redirigen al modal.
- **Badges**: `⛔N` (bloqueadores abiertos), `🔓` (desbloqueador), `●` (recién tocado, `last_touched_at<24h`), chip de origen, `⚠` stale.
- **+ Nuevo ítem**: modal rápido → `POST /api/v1/items` (status `backlog`, origen `humano`).

### `/prioridad` — matriz impacto×esfuerzo

```
   Impacto
    5 ┤ ● JWT refresh    │ ● Rediseño gradebook
    4 ┤ ● Typo  ● GIN    │ ○ Multi-tenant
      │ ── QUICK WINS ───┼─── APUESTAS GRANDES ──
    3 ┤ ○ Doc onboarding │ ○ Refactor middleware
      │ ── RELLENO ──────┼─── EVITAR ────────────
    1 ┤ ○ Favicon        │ ○ Reescribir CSS
      └────────────────────────────────────────── Esfuerzo
        XS   S    M    L    XL
  (● = ítem; color/tamaño = priority; clic→detalle; sin estimar→banda inferior)
```

- Render HTML/CSS grid o SVG **server-side** (sin build JS). Cuadrante sup-izq sombreado = quickwins (`impact_ai>=4 AND effort_ai IN ('XS','S')`).
- Lista priorizada debajo con edición inline + `↑/↓` que ajustan `priority` un escalón. **No** se añade columna `rank` libre (evita migración no pedida); el orden fino es `priority`+`impacto`.

### Contratos que el spec fija (decisiones antes abiertas)

1. Transiciones humanas: `PATCH` para no-terminales, modal+`/close` para `hecho/descartado`.
2. `priority_declared`/`effort_declared` (humano) ganan al juicio IA.
3. Kanban de hilos read-only + avance por botón (sin drag&drop).
4. Toda pantalla nueva: bloque de ayuda azul + tooltips de "por qué deshabilitado".

## 0.3 Endpoints Sprint 0

Reusan lo existente: `POST /api/v1/items`, `PATCH /api/v1/items/{id}` (validar transición contra `lifecycle.valid_transition`), `POST /api/v1/items/{id}/close`. Nuevos de UI: `GET /prioridad`. Partials: `item_row_inline.html`.

---

# Sprint 1 — F2 enriquecimiento IA (prerequisito)

> Sin esto, quickwins y la capa semántica del grafo nacen vacías (`impact_ai`/`effort_ai`/`embedding` son NULL). Adelantado en la secuencia.

- El handler `enrich` (hoy stub en `app/jobs/handlers.py`) llama a Haiku para producir `impact_ai`, `effort_ai`, `impact_rationale` y los persiste en `ai_enrichments` + copia a `items`. Registra costo (`cost_usd`).
- Embeddings: rellenar `items.embedding vector(768)` con Gemini (`gemini-embedding-001`, patrón del repo efrain) vía SQL raw (la columna no está en el ORM).
- **El LLM se invoca a través de un módulo aislado y mockeable** (no `import anthropic` inline en el handler), para que los tests lo parcheen.
- **Degradación**: mientras `impact_ai` esté vacío, el orden por defecto del backlog es `prioridad` (humano), no `impacto`. La capa semántica (S2) se omite si no hay embeddings.

---

# Sprint 2 — Grafo vivo

## 2.1 Tabla `item_relationships`

```sql
CREATE TABLE item_relationships (
  source_id  UUID NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  target_id  UUID NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  relation   TEXT NOT NULL CHECK (relation IN ('blocks','requires','conflicts','related','part_of')),
  note       TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (source_id, target_id, relation),
  CONSTRAINT item_rel_no_self CHECK (source_id <> target_id)   -- anti self-loop
);
CREATE INDEX item_rel_target ON item_relationships(target_id);
-- source_id ya lo cubre el prefijo de la PK. Índice parcial para dependencias duras:
CREATE INDEX item_rel_dep ON item_relationships(target_id, source_id)
  WHERE relation IN ('blocks','requires');
-- Unicidad simétrica para conflicts/related (una sola fila por par):
CREATE UNIQUE INDEX item_rel_sym_uniq
  ON item_relationships (least(source_id,target_id), greatest(source_id,target_id), relation)
  WHERE relation IN ('conflicts','related');
```

| Relación | Dirección | Significado |
|----------|-----------|-------------|
| `blocks` | A→B | A debe resolverse antes que B |
| `requires` | A→B | A necesita que B esté hecho |
| `conflicts` | A↔B | implementar A puede romper B (simétrico) |
| `related` | A↔B | contexto compartido (simétrico) |
| `part_of` | A→B | A es sub-ítem de B (epic) |

## 2.2 Traversal — vecindad profundidad 2 (SQL corregido)

> **La CTE recursiva del spec previo era ILEGAL en PostgreSQL**: `AND i.id NOT IN (SELECT id FROM neighborhood)` viola la regla de que la referencia recursiva no puede aparecer en una subconsulta (error `recursive reference ... must not appear within a subquery`). Como la profundidad es **fija = 2**, no se necesita recursión. Versión no-recursiva (dos hops), correcta y más rápida:

```sql
WITH edges AS (   -- arco simétrico-expandido, conservando relación y dirección
  SELECT source_id AS a, target_id AS b, relation, 'out' AS dir FROM item_relationships
  UNION ALL
  SELECT target_id AS a, source_id AS b, relation, 'in'  AS dir FROM item_relationships
),
seed AS (
  SELECT id FROM items
  WHERE scope_id = :scope_id AND status NOT IN ('hecho','descartado')
),
hop1 AS (SELECT DISTINCT e.b AS id, 1 AS depth FROM edges e JOIN seed s ON e.a = s.id),
hop2 AS (SELECT DISTINCT e.b AS id, 2 AS depth FROM edges e JOIN hop1 h ON e.a = h.id)
SELECT DISTINCT ON (i.id) i.id, i.title, i.status, i.scope_id, i.impact_ai, i.effort_ai, d.depth
FROM (SELECT id,0 AS depth FROM seed
      UNION ALL SELECT id,depth FROM hop1
      UNION ALL SELECT id,depth FROM hop2) d
JOIN items i ON i.id = d.id
WHERE i.status NOT IN ('hecho','descartado')
ORDER BY i.id, d.depth;   -- DISTINCT ON conserva la menor profundidad
```

Para la capa 2 direccional ("estos ítems **bloquean** este scope" vs "este scope bloquea a estos"), la query conserva `relation` + `dir` de `edges` y se agrupa por ellos. `graph_stats.relationships` se cuenta sobre `item_relationships` **cruda** (no sobre la expansión, que duplica).

## 2.3 Bloqueo derivado (no materializado)

> El spec previo decía "marcar el target como desbloqueado", pero **no existe estado `desbloqueado`** ni columna de bloqueo. El bloqueo es **derivado del grafo**, fuente de verdad única:

```sql
-- un ítem está EFECTIVAMENTE bloqueado si tiene un arco blocks entrante cuyo source sigue abierto
SELECT 1 FROM item_relationships r
JOIN items s ON s.id = r.source_id
WHERE r.target_id = :item_id AND r.relation = 'blocks'
  AND s.status NOT IN ('hecho','descartado')
LIMIT 1;
```

Al cerrar X (`pulso_completar`/`/close`), todo target cuyo único bloqueador era X queda desbloqueado **automáticamente por esta query** — sin escribir nada en el target. Se emite un `ItemEvent(action='unblocked_by', payload={by_item: X})` como traza, y la UI muestra "Se desbloquearon N ítems". El status manual `'bloqueado'` (señal humana) y el bloqueo-por-arco (derivado) son nociones distintas; el briefing las muestra etiquetadas por separado.

## 2.4 Topological sort (`order=topologico`)

Kahn sobre el DAG de precedencia, en Python post-query. **Normalización obligatoria**: `blocks A→B` ⟹ precedencia A⟶B; `requires A→B` ⟹ precedencia **B⟶A** (invertida); `conflicts/related/part_of` no participan. **Degradación ante ciclo**: los nodos atrapados se anexan al final ordenados por impacto, con flag `has_cycle` + ids involucrados. **Invariante: `len(salida) == len(entrada)`** (nunca se pierde un ítem) — el test lo afirma.

## 2.5 Reconciliación con `items.dependencies` (texto libre existente)

> `items.dependencies TEXT` ya existe (poblado por el digest de 468 ítems). Política elegida: en Sprint 1, un paso de enriquecimiento parsea `dependencies` con LLM y materializa arcos `requires`/`blocks` en `item_relationships`; `dependencies` queda como nota human-readable de respaldo. Así el grafo arranca con las dependencias que el digest ya capturó, en vez de ignorarlas.

## 2.6 Endpoints + UI del grafo

- `POST /api/v1/items/relationships` (crear arco; rechaza self-resolución a 422), `DELETE /api/v1/items/relationships/{source}/{target}/{relation}`, `GET /api/v1/items/{id}/graph` (subgrafo, `WHERE source_id=:id OR target_id=:id`).
- **UI** (sección "Relaciones" en `/items/{id}`): lista de arcos entrantes/salientes humanizada ("es bloqueado por" / "bloquea a"), badge de status del otro ítem (arco resuelto = tachado), creador de arco (autocomplete `GET /search` + `<select>` de relación + nota), minimapa SVG server-side. Partial `relationship_list.html`.

---

# Sprint 3 — MCP-over-HTTP

## 3.1 Implementación con el SDK oficial (no a mano)

> El spec previo dejaba ambiguo SDK-vs-a-mano y la checklist empujaba a reimplementar JSON-RPC+SSE. **Decisión: usar el SDK `mcp`** (`FastMCP(stateless_http=True, json_response=True)`), montado en la FastAPI existente. El SDK maneja `initialize`, capabilities, framing, content blocks e `isError`. Agregar `mcp>=1.0` a `pyproject.toml` (hoy ausente → import falla en CI).

- Montaje: `app.mount("/mcp", mcp.streamable_http_app())`, wireando el `session_manager.run()` del SDK al `lifespan` combinado de `create_app()` (issue conocido del SDK al montar en FastAPI existente — es una tarea, no "just works").
- **Modo stateless + `initialize` siguen siendo compatibles**: sin `Mcp-Session-Id` ni estado persistente, pero `initialize` se re-negocia por conexión. `json_response=True` → responde `application/json` (sin SSE manual; los 9 tools no necesitan streaming server→cliente). GET/DELETE a `/mcp` → `405` (lo cubre el SDK).
- `protocolVersion` exacta: `"2025-03-26"`.
- **Validación de `Origin`** (MUST de la spec MCP, anti DNS-rebinding): allow-list; permitir ausencia de `Origin` (Claude Code server-to-server no lo envía) pero rechazar orígenes de navegador no confiables.

## 3.2 Auth por scope (corrige agujero de authz)

`ApiToken.scopes` es `read|write`. **Enforcement explícito por tool**: tools de lectura aceptan `read|write`; tools de escritura exigen `write` → si el token es `read`, el tool devuelve `isError:true` (o 403). Contrato: token inválido/revocado → 401; scope insuficiente → 403. El actor de las escrituras se resuelve `ApiToken.created_by → User.email` (o literal documentado), no `token:<name>` opaco. **Cada escritura MCP emite `ItemEvent`** con ese actor.

`isError` vs error JSON-RPC: fallos de negocio (sin match, scope inexistente, arco duplicado) → `result` con `isError:true` y texto accionable. Errores JSON-RPC (`-32602`/`-32603`) solo para args malformados o fallo interno. (El SDK mapea excepciones del handler a `isError` automáticamente.)

## 3.3 Tools

Cada tool corre sus queries **secuencialmente sobre la misma `AsyncSession`** (una AsyncSession no es concurrency-safe; nada de `gather` sobre la misma sesión).

- **`pulso_contexto(scope?, work_description?)`** → tres capas:
  - **Local**: quickwins (`impact_ai>=4 AND effort_ai IN ('XS','S')`, con fallback a `priority IN ('p0','p1')` si `impact_ai` vacío), bloqueadores (status manual `'bloqueado'` + bloqueo-derivado §2.3), bugs Sentry sin ítem (`sentry_issues WHERE item_id IS NULL`), hilos en `stage='en-desarrollo'`.
  - **Vecindad** (§2.2): arcos `blocks`/`conflicts` hacia/desde el scope, agrupados por dirección.
  - **Semántica** (pgvector): solo si `work_description` se pasa **y** hay embeddings. Degrada a `semantic: null, semantic_status: "pendiente-f2"` si la columna está vacía. **No rompe** las capas 1-2.
- **`pulso_buscar(q, scope?, tipo?, limit=10)`** — full-text GIN existente. Devuelve `summary_md` (no "summary").
- **`pulso_crear(title, summary, type, scope_name, effort_ai?, impact_ai?, origen='ia-sesion')`** — **`origen='ia-sesion'`** (NO `'claude-code'`, que viola el CHECK). Crea scope si no existe.
- **`pulso_avanzar(item_id|query, to_status)`** — apertura/transición validada (`lifecycle.valid_transition`). El verbo que faltaba.
- **`pulso_completar(item_id|search_query, nota?, commit_sha?)`** — **llama al servicio de `POST /{id}/close`** (no duplica la lógica). Si `search_query` resuelve a **empate de rank en el top-2 → aborta con `isError`** ("ambiguo, especifica item_id") — no adivina (patrón delete-single-row del proyecto). `nota`+`commit_sha` a `source_refs`.
- **`pulso_relacionar(source_id|query, target_id|query, relation, note?)`** — mismo guard de ambigüedad; rechaza self-loop.
- **`pulso_listar(scope?, status[]?, tipo?, order='impacto'|'prioridad'|'topologico', quickwins?, limit=20)`**.
- **`pulso_hilo_crear / pulso_hilo_avanzar / pulso_hilo_listar`** (S4).

## 3.4 Prompts y Resources (corregidos)

- **Prompts** (`prompts/list` + `prompts/get`, identificados por `name`, NO por URI): `briefing` (arg opcional `work_description`) y `decision` (arg `topic`) — `decision` consulta **`item_comments WHERE kind='decision'`** (ya existe; **no** `type=decisión` ni `decision_log`, que no existen).
- **Resource templates** (`resources/templates/list` con `uriTemplate` RFC 6570, no `resources/list` con la llave literal): `pulso://scope/{scope_name}`, `pulso://graph/{item_id}`. `resources/read` parsea la URI concreta.

## 3.5 Configuración del cliente (CORREGIDA — era Claude Desktop)

> **El spec previo apuntaba a `~/.claude/claude_desktop_config.json` con `"type":"http"` — ese es Claude DESKTOP. Claude CODE (CLI) NO lee ese archivo.** Tal como estaba, ningún Claude Code se conectaría jamás.

**Forma recomendada** (escribe en `~/.claude.json`, cumple "solo un token, sin instalar nada"):
```bash
claude mcp add --transport http pulso https://pulso.example.com/mcp \
  --header "Authorization: Bearer <API_TOKEN>"
```

**Para compartir con el equipo vía git** — `.mcp.json` versionado en la raíz del repo efrain (token desde entorno, no hardcodeado):
```json
{
  "mcpServers": {
    "pulso": {
      "type": "http",
      "url": "https://pulso.example.com/mcp",
      "headers": { "Authorization": "Bearer ${PULSO_TOKEN}" }
    }
  }
}
```
Claude Code expande `${PULSO_TOKEN}` desde el entorno; servers de scope `project` piden aprobación interactiva la primera vez. El token se genera desde `/admin` (acción "Generar token MCP" que muestra el secreto una vez + el snippet copiable).

## 3.6 El conducto como pauta (reconciliado con trunk-based)

> El CLAUDE.md de efrain es trunk-based (commit directo a `main`, deploy por tag). La pauta es **best-effort, no bloqueante**; si Pulso está caído, la sesión continúa y el **webhook GitHub (S5) es el fallback** (`pulso:UUID` en el commit cierra el ítem aunque el agente no llame `pulso_completar`).

Sección a agregar en CLAUDE.md de efrain:
```markdown
## Pulso — conducto pre/post sesión (best-effort)
Inicio: pulso_contexto(scope="<scope>", work_description="<qué harás>"). Revisa la
  vecindad: ítems de otros scopes que bloquean/conflictúan. Si Pulso no responde, continúa.
Durante: si descubres una dependencia → pulso_relacionar(A,B,"blocks",nota). Deuda nueva
  → pulso_crear(...). Para el flujo rápido (80% idea→deploy) NO uses Hilos.
Cierre (tras push a main o merge): pulso_completar(item|query, nota, commit_sha=<sha>)
  por cada ítem resuelto. El webhook Git respalda esto si lo olvidas.
```

---

# Sprint 4 — Hilos (funnel del 20% pesado)

## 4.1 Modelo

```sql
CREATE TABLE threads (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scope_id UUID NOT NULL REFERENCES scopes(id),
  title TEXT NOT NULL, summary_md TEXT,
  stage TEXT NOT NULL DEFAULT 'idea' CHECK (
    stage IN ('idea','investigacion','historias','spec','en-desarrollo','review','hecho','descartado')),
  assignee_user_id UUID REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE thread_artifacts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  thread_id UUID NOT NULL REFERENCES threads(id),
  stage TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('investigacion','historias','spec','notas','decision')),
  content_md TEXT NOT NULL,
  created_by_user_id UUID REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE items ADD COLUMN thread_id UUID REFERENCES threads(id);
```
> **Sin tildes en valores de enum** (`investigacion`, `decision`) — el repo ya usa `'decision'` sin tilde en `item_comments_kind_check`; se mantiene la convención para no tener `decision`/`decisión` coexistiendo. `assignee_user_id` nullable (un thread creado vía MCP/token puede no tener `User` humano). **`threads.stage` ≠ `items.status`**: vocabularios distintos, no se cruzan (el "en-desarrollo" del hilo no filtra items por status).

## 4.2 Stages, UI y elaborate-stage

- `idea → investigacion → historias → spec → en-desarrollo → review → hecho` (+ `descartado` desde cualquiera).
- **Kanban read-only** (`/hilos`, 7 columnas + descartado colapsada). Movimiento por botón en `/hilos/{id}` (sin drag&drop): "Avanzar a X" / "Volver a Y" / "Descartar". `Avanzar` `disabled` con `title` si falta el artifact del stage, o si en `review→hecho` hay ítems linkeados abiertos.
- **`elaborate-stage` (IA)**: `POST /api/v1/threads/{id}/elaborate-stage` (Haiku para historias, Sonnet para spec) genera un **draft editable** del siguiente stage → "Generando…" (spinner) → tarjeta amarilla editable → "Guardar y avanzar" persiste el artifact (editado) + avanza / "Descartar borrador". Falla IA → banner rojo, costo al "Costo IA del mes".
- Ítems linkeados por `thread_id`; "+ Crear ítem en este hilo" presetea `thread_id`.

---

# Sprint 5 — Webhooks (Sentry + GitHub, firmados)

## 5.1 Sentry (`POST /api/v1/webhooks/sentry`) — usa `sentry_issues`

> **La tabla `sentry_issues` (UNIQUE `sentry_issue_id`, triage, item_id FK) ya existe.** El flujo escribe ahí, NO crea ítems en paralelo (evita doble fuente de verdad).

1. **Verificar `Sentry-Hook-Signature`** (HMAC-SHA256 del body crudo con el client secret) ANTES de parsear. Falta/mismatch → 401. *(Sin esto, cualquiera inyecta ítems desde internet — era un blocker.)*
2. **Upsert en `sentry_issues`** por `sentry_issue_id` UNIQUE (idempotencia nativa ante reentregas). Incrementa `events_count`, actualiza `last_seen`.
3. Triage Haiku asíncrono (worker, LLM mockeable) → `triage`. **Solo `triage='bug-real'` promueve a `item`** (`type=bug`, `origen='sentry'`), poblando `sentry_issues.item_id`. Opcional: arco `blocks` automático bug→ítem afectado.
4. `pulso_contexto` capa local lee `sentry_issues WHERE item_id IS NULL`.
5. Sanitizar/truncar el stack trace en ingesta (strip HTML) — es contenido no confiable que se renderiza en la UI (ver Seguridad).

## 5.2 GitHub (`POST /api/v1/webhooks/github`)

1. **Verificar `X-Hub-Signature-256`** (HMAC-SHA256 del body con el webhook secret). Falta/mismatch → 401. *(Sin esto, cualquiera cierra ítems del backlog — era un blocker.)*
2. **Idempotencia** por `X-GitHub-Delivery` persistido; `pulso_completar` no-op si el ítem ya está `hecho`.
3. `fix(auth):` → marca `last_touched_at` de ítems del scope. `pulso:UUID` / `closes pulso:UUID` → completar **validando** que el UUID existe y está cerrable (si ya cerrado/descartado, no-op; loguear, nunca fallar el webhook entero por un UUID inválido).
4. Emite `ItemEvent(actor='github:<sha>', action='completed')`.

## 5.3 Secretos

`SENTRY_CLIENT_SECRET` y `GITHUB_WEBHOOK_SECRET` en `infra/.env` del VM (gitignored), leídos vía settings; fail-fast en boot si el webhook está habilitado y faltan.

---

# Modelos ORM (obligatorio — los tests usan `create_all`, no migraciones)

> Sin las clases ORM, las tablas nuevas no existen en los tests. Especificar en `app/items/models.py` (relationships) y `app/threads/models.py`:

- **`ItemRelationship`**: PK compuesto `(source_id, target_id, relation)` (tres `mapped_column(..., primary_key=True)`), `note`, `created_at`. CheckConstraints `relation` + `no_self`.
- **`Thread`**, **`ThreadArtifact`**: con CheckConstraints de `stage`/`kind`, `default=uuid.uuid4` Python-side.
- **`Item`**: agregar `thread_id` (FK nullable) y `last_touched_at` (TIMESTAMPTZ nullable; semántica = "tocado por push/sesión", distinta de `updated_at` que dispara con cualquier UPDATE).

`source_refs` es **`JSON`, no `JSONB`**: las queries por clave (dedup Sentry) castean `source_refs::jsonb ->> 'k'`, **o** S5 migra la columna a `JSONB` + índice GIN (recomendado si hay volumen).

---

# Migraciones Alembic (head real: v0002)

| Versión | down_revision | Descripción |
|---------|---------------|-------------|
| v0003 | v0002 | `item_relationships` + índices + CHECKs (Sprint 2) |
| v0004 | v0003 | `items.last_touched_at` + (opcional) `source_refs`→JSONB (Sprints 0/5) |
| v0005 | v0004 | `threads` + `thread_artifacts` + `items.thread_id` (Sprint 4) |

`downgrade()` no-trivial (drop de tablas/columnas). Verificar `alembic heads = 1` antes de cada PR.

---

# Estrategia de tests

> "1 test por tool + auth" es insuficiente. Casos donde viven los bugs (priorizados):

**Grafo:** ciclo A→B→C→A en vecindad (termina, cada ítem una vez); vecindad con 0 arcos (solo semilla); `conflicts` simétrico visible desde ambos scopes; topo-sort con ciclo (**`len(salida)==len(entrada)`**, no pierde ítems); self-loop rechazado (422); arco duplicado (409/idempotente, no 500); auto-desbloqueo (target desbloqueado por query derivada + `ItemEvent`, arco intacto).

**MCP:** test del transporte **sin Claude Code real** — `POST /mcp` con sobre JSON-RPC crudo vía `httpx.AsyncClient`: `initialize` (assert capabilities), `tools/list` (9 tools), `tools/call` (assert `content`). Token `read` llamando tool de escritura → 403/`isError` (agujero de authz). Token revocado entre llamadas → 401. `pulso_completar(search_query)` ambiguo (empate de rank) → aborta, no cierra el ítem equivocado. Negociación `Accept` (json vs sse).

**Lifecycle:** transición inválida rechazada; cada transición válida emite `ItemEvent`.

**Webhooks:** firma Sentry/GitHub inválida → 401; dedup Sentry por `sentry_issue_id` UNIQUE (reentrega → 1 issue, no 2); idempotencia GitHub por delivery-id; `pulso:UUID` inexistente → no 500.

**Infra de test:** `mcp>=1.0` en deps (sin esto CI rojo al import). LLM mockeado (módulo aislado, no API real). `result.rowcount` lleva `# type: ignore[attr-defined]` (mypy). Introducir `pytest-cov` + baseline `--cov-fail-under` medido ahora (el repo aún no lo tiene; este feature es buen momento para fijarlo).

---

# Seguridad (transversal)

- **3 superficies de escritura a internet** (`/mcp`, webhook Sentry, webhook GitHub) — las tres requieren auth/firma antes de cualquier efecto. `/mcp`: Bearer + scope; webhooks: HMAC.
- **Rate limiting** por token (`/mcp`) y por IP (webhooks) en Caddy o app-level — `/mcp` dispara trabajo caro (pgvector, enqueue Haiku con costo $).
- **XSS almacenado**: `summary_md` y `thread_artifacts.content_md` pasan a ser atacante-controlados (webhook Sentry). Mantener autoescape de Jinja2; si se renderiza markdown→HTML, pasar por **nh3/bleach con allowlist** — nunca `| safe` sobre contenido de webhook. Tratar `origen IN ('sentry','agente')` como no confiable.
- **Auditoría**: toda mutación vía MCP/webhook emite `ItemEvent` con actor identificable (`token:<email>`, `sentry:<id>`, `github:<sha>`).

---

# Checklist de done (por sprint)

**Sprint 0 (PRIORIDAD 1):** `lifecycle.valid_transition` + tests de matriz; selector de transición en `/items/{id}`; modal cierre con motivo real; Reabrir; edición inline status/priority en `/backlog` (+ partial); orden `prioridad`/filtros origen+tipo+bloqueados; `/prioridad` matriz; "+ Nuevo ítem"; ayuda + tooltips (DoD). Sin migración de schema (o solo `last_touched_at`).

**Sprint 1:** handler `enrich` real (Haiku, mockeable) → `impact_ai`/`effort_ai`/`rationale`; embeddings Gemini a `items.embedding`; parse de `dependencies`→arcos; fallback de orden sin F2.

**Sprint 2:** migración v0003 + modelos ORM; endpoints relationships (POST/DELETE/graph); query de vecindad no-recursiva; bloqueo derivado + `ItemEvent`; Kahn con degradación; UI sección Relaciones + minimapa + badges; tests de grafo.

**Sprint 3:** `mcp>=1.0` en deps; `/mcp` vía `FastMCP` montado en lifespan; `initialize`/capabilities/`405`/Origin; 11 tools; prompts (briefing, decision→item_comments) + resource templates; auth scope read/write (401/403); `isError` vs JSON-RPC; `pulso_completar`→servicio close + guard de ambigüedad; config `claude mcp add`/`.mcp.json` en README; sección CLAUDE.md efrain; tests de transporte.

**Sprint 4:** migración v0005 + modelos ORM; CRUD threads + artifacts; `elaborate-stage` (Haiku/Sonnet, mockeable); UI `/hilos` Kanban + `/hilos/{id}` con avance/draft.

**Sprint 5:** webhook Sentry firmado + upsert `sentry_issues` + promoción `bug-real`→item; webhook GitHub firmado + idempotente + `pulso:UUID` validado; secretos en `infra/.env`; sanitización de contenido no confiable; tests de firma/dedup.

---

# Fuera de alcance (futuro)

- Autonomous fix agent (F6): ítem `agent_ready=true` → runner que abre PR (gateado por aprobación). LangGraph entra aquí (flujo cíclico), no antes.
- Métricas/velocidad, public roadmap: pantallas futuras (datos ya se acumulan).
- Multi-repo/scope en webhooks GitHub: validar que el repo tiene permiso sobre el scope del ítem.
- Rank libre arrastrable (drag-rank): requiere columna `rank` — migración no pedida; hoy el orden fino es `priority`+impacto.
