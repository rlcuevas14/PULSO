# Pulso F1.5 + F2.5: MCP-over-HTTP + Grafo de relaciones + Hilos de Desarrollo

**Goal:** Convertir Pulso en el conducto bidireccional entre sesiones de Claude Code y el backlog de producto. El backlog se modela como un grafo de ítems interconectados; el MCP expone ese grafo al agente en tiempo real sin caché estática ni artefactos de "visión global" — el contexto se construye on-demand a partir del grafo vivo.

**Arquitectura:** Endpoint `/mcp` (MCP Streamable HTTP 2025-03). Grafo explícito en tabla `item_relationships` (arcos tipados). Contexto de sesión en tres capas: local (scope activo), vecindad (CTE recursiva sobre el grafo, profundidad 2), semántica (pgvector). Hilos como contenedores de stage para features pesadas. Todo en PostgreSQL — sin Neo4j, sin LangGraph, sin actualización periódica.

**Tech Stack:** FastAPI + SSE, MCP SDK Python (`mcp>=1.0`), `item_relationships` + `threads` + `thread_artifacts`, pgvector ya instalado, CTEs recursivas PostgreSQL para traversal.

**Principio rector:** el grafo es siempre fresco porque las queries corren contra los datos vivos. No hay snapshot semanal ni artefacto de resumen — `pulso_contexto()` lee el estado real en el momento de la llamada.

---

## Contexto y motivación

Hoy cada sesión de Claude Code empieza en frío leyendo BACKLOG.md y termina sin dejar rastro estructurado. El problema no es solo visibilidad — es **context collapse**: el agente optimiza localmente (la feature del día) sin ver tensiones globales (el ítem en otro scope que depende del mismo módulo).

La solución no es GraphRAG sobre corpus no estructurado ni una visión global generada periódicamente (riesgo de desactualización). La solución es un **grafo explícito vivo**: cada arco `blocks`/`requires`/`conflicts` se registra cuando se descubre, y `pulso_contexto()` lo traversa en tiempo real. PostgreSQL con CTEs recursivas maneja grafos de miles de nodos sin infraestructura adicional.

---

## Parte A: Grafo de relaciones entre ítems

### A.1 Tabla `item_relationships`

```sql
CREATE TABLE item_relationships (
  source_id  UUID NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  target_id  UUID NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  relation   TEXT NOT NULL CHECK (
    relation IN ('blocks','requires','conflicts','related','part_of')
  ),
  note       TEXT,  -- contexto opcional de por qué existe esta relación
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (source_id, target_id, relation)
);

CREATE INDEX item_rel_target ON item_relationships(target_id);
```

**Semántica de arcos:**

| Relación | Dirección | Significado |
|----------|-----------|-------------|
| `blocks` | A → B | A debe resolverse antes que B pueda avanzar |
| `requires` | A → B | A necesita que B esté hecho para funcionar correctamente |
| `conflicts` | A ↔ B | Implementar A puede romper B (simétrico — se almacena solo una dirección, se consulta ambas) |
| `related` | A ↔ B | Contexto compartido sin dependencia dura |
| `part_of` | A → B | A es un sub-ítem de B (B es el padre/epic) |

### A.2 Traversal: CTE recursiva para vecindad

La query que usa `pulso_contexto()` para obtener el "cluster de comunidad" de un scope:

```sql
WITH RECURSIVE neighborhood AS (
  -- semilla: ítems del scope activo
  SELECT i.id, i.title, i.status, i.scope_id, i.impact_ai, i.effort_ai, 0 AS depth
  FROM items i
  WHERE i.scope_id = :scope_id AND i.status NOT IN ('hecho', 'descartado')

  UNION ALL

  -- vecinos via arcos de cualquier tipo (salientes y entrantes)
  SELECT i.id, i.title, i.status, i.scope_id, i.impact_ai, i.effort_ai, n.depth + 1
  FROM items i
  JOIN (
    SELECT target_id AS neighbor_id, source_id AS origin_id FROM item_relationships
    UNION ALL
    SELECT source_id AS neighbor_id, target_id AS origin_id FROM item_relationships
  ) r ON r.neighbor_id = i.id
  JOIN neighborhood n ON n.id = r.origin_id
  WHERE n.depth < 2  -- profundidad 2 = vecinos directos + sus vecinos
    AND i.id NOT IN (SELECT id FROM neighborhood)
    AND i.status NOT IN ('hecho', 'descartado')
)
SELECT DISTINCT ON (id) * FROM neighborhood ORDER BY id, depth;
```

### A.3 Topological sort para `pulso_listar`

Cuando `order=topologico`, los ítems se ordenan respetando arcos `blocks`/`requires`:
los ítems sin desbloqueadores pendientes aparecen primero. Implementado como Kahn's algorithm sobre el subgrafo filtrado (Python, post-query).

### A.4 Tool nuevo: `pulso_relacionar`

```
pulso_relacionar(
  source_id: uuid | source_query: string,
  target_id: uuid | target_query: string,
  relation: enum[blocks|requires|conflicts|related|part_of],
  note?: string
) → Relationship
```

Claude Code llama este tool cuando durante el desarrollo descubre que "el ítem X está bloqueado por Y" o "implementar A puede conflictuar con B". El grafo se construye incrementalmente, sesión a sesión.

---

## Parte B: MCP HTTP endpoint

### B.1 Protocolo

MCP Streamable HTTP (2025-03):
- **Endpoint único**: `POST /mcp` — acepta JSON-RPC requests, devuelve SSE o JSON directo
- **Autenticación**: `Authorization: Bearer <api_token>` — reutiliza ApiToken existente
- **Stateless**: sin session ID persistente (compatible con reverse proxy)
- **Content-Type negotiate**: `text/event-stream` → SSE; `application/json` → JSON directo

### B.2 Tools

#### `pulso_contexto(scope?: string, work_description?: string) → SessionBriefing`

La pieza central. Corre tres queries en paralelo y devuelve un objeto estructurado:

**Capa 1 — Local** (scope activo):
- Top-5 quickwins: `impact_ai >= 4 AND effort_ai IN ('XS','S') AND status NOT IN ('hecho','descartado')`
- Bloqueadores activos: `status = 'bloqueado'`
- Hilos en `stage = 'en-desarrollo'`

**Capa 2 — Vecindad** (grafo, CTE recursiva profundidad 2):
- Ítems en otros scopes con arcos `blocks`/`conflicts` hacia/desde el scope activo
- Se muestran agrupados por tipo de relación: "estos ítems bloquean trabajo en este scope", "estos ítems podrían conflictuar"

**Capa 3 — Semántica** (pgvector):
- Solo si `work_description` se pasa (descripción breve de qué se va a trabajar hoy)
- Top-5 ítems por similitud coseno al embedding de `work_description`
- Permite: "voy a trabajar en JWT refresh" → Pulso surfacea ítems semánticamente relacionados aunque estén en otros scopes sin arcos explícitos

Retorno (JSON → markdown formateado para el briefing):
```json
{
  "local": { "quickwins": [...], "blockers": [...], "active_threads": [...] },
  "neighborhood": { "blocks_this_scope": [...], "conflicts": [...] },
  "semantic": [...],  // solo si work_description fue pasado
  "graph_stats": { "total_open": 42, "relationships": 17 }
}
```

#### `pulso_buscar(q: string, scope?: string, tipo?: string, limit?: int=10) → Item[]`

Full-text sobre índice GIN (v0002). Retorna título, summary, status, effort_ai, impact_ai, scope, relaciones de primer grado.

#### `pulso_crear(title, summary, type, scope_name, effort_ai?, impact_ai?, origen="claude-code") → Item`

Crea ítem. `scope_name` puede ser nuevo (se crea el scope). Si no se pasan `effort_ai`/`impact_ai`, el worker de enriquecimiento los llena (F2).

#### `pulso_completar(item_id|search_query, nota?, commit_sha?) → Item`

Marca `hecho`. Si se pasa `search_query`, busca por full-text y toma el match de mayor rank. `nota` + `commit_sha` quedan en `source_refs` para trazabilidad. Si el ítem tenía arcos `blocks` salientes, Pulso marca automáticamente esos ítems target como "desbloqueados" (el arco no se borra — queda como historial, pero se agrega nota).

#### `pulso_listar(scope?, status[]?, tipo?, order="impacto"|"topologico", quickwins?, limit=20) → Item[]`

`order=topologico` aplica Kahn's algorithm sobre el subgrafo filtrado — los ítems sin dependencias pendientes van primero.

#### `pulso_relacionar(source_id|source_query, target_id|target_query, relation, note?) → Relationship`

Registra un arco en el grafo. Si se pasan queries de texto, resuelve cada uno por full-text (top-1 match). Retorna los dos ítems involucrados y el arco creado.

#### `pulso_hilo_crear(title, summary, scope_name) → Thread`

Crea Hilo en stage `idea`.

#### `pulso_hilo_avanzar(thread_id, artifact?: {stage, content}) → Thread`

Avanza al siguiente stage. El `artifact` se guarda como evidencia del stage actual.

#### `pulso_hilo_listar(stage?, scope?) → Thread[]`

Lista Hilos con stage actual y último artifact.

### B.3 Prompts

```
pulso://briefing
```
Prompt MCP preformateado para inyección al inicio de sesión. Claude Code lo incluye en su contexto de sistema. Internamente llama `pulso_contexto()` con las capas local + vecindad (sin semántica — la capa semántica se activa solo si Claude Code pasa `work_description` explícitamente).

```
pulso://decision/{topic}
```
Busca ítems con `type=decisión` o `decision_log=true` sobre el topic. Retorna razonamiento registrado. Claude Code lo consulta antes de tomar decisiones de arquitectura.

### B.4 Resources

```
pulso://scope/{scope_name}
```
Vista completa: ítems por status, relaciones de primer grado, hilos activos.

```
pulso://graph/{item_id}
```
Subgrafo centrado en un ítem: sus vecinos de profundidad 2, tipos de arcos, ítems bloqueados/requeridos.

### B.5 Configuración del cliente

`~/.claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "pulso": {
      "type": "http",
      "url": "https://pulso.eduk3.cl/mcp",
      "headers": {
        "Authorization": "Bearer <API_TOKEN>"
      }
    }
  }
}
```

El token se genera desde la pantalla Admin (sistema ApiToken existente).

### B.6 Sección a agregar en CLAUDE.md del repo efrain

```markdown
## Pulso — conducto pre/post sesión

Al **inicio de sesión**:
1. Llamar `pulso_contexto(scope="<scope_activo>", work_description="<qué se va a trabajar>")`.
2. Revisar la capa de vecindad: si hay ítems de otros scopes que bloquean o conflictúan, 
   considerarlos antes de diseñar la solución.
3. Si hay hilos en `en-desarrollo` para este scope, revisarlos.

Durante el **desarrollo**:
- Si se descubre que un ítem depende de otro: `pulso_relacionar(A, B, "blocks", nota)`.
- Si se descubre deuda técnica nueva: `pulso_crear(...)`.

Al **cierre de sesión** (tras push/merge exitoso):
1. `pulso_completar(item_id_o_query, nota="<descripción breve>", commit_sha="<sha>")` 
   para cada ítem resuelto.
2. Si un ítem resuelto desbloqueaba otros, verificar que esos ítems quedaron 
   sin el arco de bloqueo pendiente (Pulso lo marca automáticamente, pero confirmar).
```

---

## Parte C: Hilos de desarrollo (Thread funnel)

### C.1 Modelo de datos

```sql
CREATE TABLE threads (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scope_id         UUID NOT NULL REFERENCES scopes(id),
  title            TEXT NOT NULL,
  summary_md       TEXT,
  stage            TEXT NOT NULL DEFAULT 'idea' CHECK (
    stage IN ('idea','investigación','historias','spec','en-desarrollo','review','hecho','descartado')
  ),
  assignee_user_id UUID REFERENCES users(id),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE thread_artifacts (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  thread_id           UUID NOT NULL REFERENCES threads(id),
  stage               TEXT NOT NULL,
  kind                TEXT NOT NULL CHECK (kind IN ('investigación','historias','spec','notas','decisión')),
  content_md          TEXT NOT NULL,
  created_by_user_id  UUID REFERENCES users(id),
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ítems de implementación linkeados al hilo que los originó
ALTER TABLE items ADD COLUMN thread_id UUID REFERENCES threads(id);
```

### C.2 Stages

```
idea → investigación → historias → spec → en-desarrollo → review → hecho
  ↓          ↓              ↓         ↓           ↓             ↓
                         descartado (desde cualquier stage)
```

- **idea**: título + summary. Origen puede ser un ítem existente promovido a Hilo.
- **investigación**: artifact con contexto — sistemas similares, código relevante, decisiones pasadas que aplican.
- **historias**: artifact con user stories `Como [rol], quiero [qué], para [por qué]`.
- **spec**: artifact técnico (equivalente a docs/superpowers/specs/). Claude puede generarlo desde investigación + historias via `elaborate-stage`.
- **en-desarrollo**: ítems de impl. creados y linkeados con `thread_id`. El hilo sabe si hay trabajo activo.
- **review**: checklist de QA / eyeball del owner.
- **hecho**: todos los ítems linkeados en status `hecho`.

### C.3 Generación AI de artifact (`elaborate-stage`)

`POST /api/v1/threads/{id}/elaborate-stage`:
1. Lee el stage actual y todos los artifacts existentes del hilo
2. Llama Haiku (para historias) o Sonnet (para spec) con el contexto del hilo
3. Guarda resultado como artifact draft del siguiente stage
4. El owner edita y confirma → `pulso_hilo_avanzar()` aplica el avance

### C.4 Relación Hilos ↔ Grafo

Cuando un Hilo está en `en-desarrollo`, los ítems linkeados (`thread_id`) pueden tener arcos `part_of` hacia el Hilo (si se modela el Hilo como un nodo especial) o simplemente coexistir sin arcos explícitos — el `thread_id` FK ya da la agrupación. La segunda opción es más simple y suficiente.

### C.5 UI

- `/hilos` — lista Kanban por stage, con filtro por scope
- `/hilos/{id}` — detalle: artifacts por stage (timeline), lista de ítems linkeados, botón avanzar stage, botón elaborate-stage (AI)

---

## Parte D: Sentry webhook

`POST /api/v1/webhooks/sentry` — recibe alertas de Sentry.

1. Si existe ítem con `source_refs.sentry_fingerprint == event.fingerprint`: bump `stale_risk=true`, agrega ocurrencia.
2. Si es nuevo: crea ítem `type=bug, origen=sentry`, title = error message, summary = stack trace truncado + URL Sentry, scope derivado de transaction path.
3. Haiku triage asíncrono (worker): rellena `impact_ai` (affected users), `effort_ai`, `impact_rationale`.
4. `pulso_contexto()` incluye bugs recientes de Sentry sin ítem asociado en la capa local.

---

## Parte E: Git webhook → progreso automático

`POST /api/v1/webhooks/github` — push/PR merge.

1. `fix(auth):` → scope `auth` → ítems del scope marcados `last_touched_at`.
2. `pulso:UUID` en commit message → `pulso_completar` automático con el SHA.
3. PR merged con `closes pulso:UUID` en body → mismo efecto.

---

## Migraciones Alembic

| Versión | Descripción |
|---------|-------------|
| v0003 | `item_relationships` + índices |
| v0004 | `threads` + `thread_artifacts` |
| v0005 | `items.thread_id` FK + `items.last_touched_at` |

---

## Fuera del sprint

- Kahn's topological sort: implementado en Python post-query (no requiere migración)
- Embedding automático de ítems nuevos para capa semántica: depende de F2 (enriquecimiento con Haiku)
- Velocidad / métricas: pantalla futura
- Public roadmap: pantalla futura
- Autonomous fix agent (F4): post-validación de F3
- LangGraph: cuando llegue F4 (agente autónomo con flujo cíclico)

---

## Checklist de done

**Grafo (A)**
- [ ] Migración v0003: tabla `item_relationships` + índices
- [ ] Endpoints REST: `POST /api/v1/items/relationships`, `GET /api/v1/items/{id}/graph`
- [ ] CTE recursiva en `pulso_contexto()` capa vecindad
- [ ] Kahn sort en `pulso_listar(order=topologico)`
- [ ] Tool `pulso_relacionar` en MCP
- [ ] Tests: crear arco, traversal (mock DB), ciclo sin bloqueo infinito

**MCP (B)**
- [ ] `POST /mcp` con protocolo JSON-RPC 2.0 + SSE
- [ ] 9 tools: contexto (3 capas), buscar, crear, completar, listar, relacionar, hilo_crear, hilo_avanzar, hilo_listar
- [ ] 2 prompts (briefing, decision) + 2 resources (scope, graph)
- [ ] Auth Bearer token (ApiToken existente), token inválido → 401
- [ ] Tests: 1 por tool + auth
- [ ] `claude_desktop_config.json` documentado en README
- [ ] Sección CLAUDE.md repo efrain con protocolo pre/post sesión

**Hilos (C)**
- [ ] Migraciones v0004 + v0005
- [ ] Endpoints REST CRUD threads + artifacts
- [ ] `elaborate-stage` con AI (Haiku/Sonnet)
- [ ] UI `/hilos` + `/hilos/{id}`

**Webhooks (D + E)**
- [ ] Sentry: crear ítem desde alert, dedup por fingerprint
- [ ] GitHub: `pulso:UUID` en commit → auto-completar
