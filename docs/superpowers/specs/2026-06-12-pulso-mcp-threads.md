# Pulso F1.5 + F2.5: MCP-over-HTTP + Hilos de Desarrollo

**Goal:** Convertir Pulso en el conducto bidireccional entre sesiones de Claude Code y el backlog de producto, con MCP nativo sobre HTTP y un modelo de Hilos (epics con stages de desarrollo).

**Arquitectura:** Endpoint `/mcp` en la API de Pulso implementando el protocolo MCP Streamable HTTP (spec 2025-03). Los Hilos son una capa encima de los Items existentes: un Hilo agrupa ítems por stage y soporta artifacts por stage (notas, spec, historias). El cliente es Claude Code vía `claude_desktop_config.json`.

**Tech Stack:** FastAPI + SSE (`anyio`), MCP protocol JSON-RPC 2.0, `mcp` SDK Python, tablas `threads` + `thread_artifacts`, no cambia el modelo de Items.

---

## Contexto y motivación

Hoy cada sesión de Claude Code empieza en frío leyendo BACKLOG.md y termina sin dejar rastro estructurado. Pulso ya tiene los datos; le falta el conducto.

El MCP-over-HTTP resuelve esto: Claude Code se conecta a `pulso.eduk3.cl/mcp` con un API token, y tiene acceso a tools + prompts que le dan contexto pre-sesión y le permiten escribir progreso post-sesión.

Los Hilos resuelven el problema del funnel para features pesadas: una idea no pasa directamente a dev — pasa por investigación, historias de usuario, spec y luego desarrollo. Esto habilita trabajo colaborativo futuro (distintas personas en distintos stages) sin cambiar la arquitectura de Items existente.

---

## Parte A: MCP HTTP endpoint

### A.1 Protocolo

MCP Streamable HTTP (2025-03):
- **Endpoint único**: `POST /mcp` — acepta JSON-RPC requests y devuelve SSE o JSON directo
- **Autenticación**: `Authorization: Bearer <api_token>` — reutiliza el sistema de ApiToken existente
- **Sesión stateless**: no hay session ID persistente entre requests (compatible con reverse proxy y múltiples workers)
- **Content-Type negotiate**: si el cliente acepta `text/event-stream`, responde SSE; si no, JSON directo

### A.2 Capacidades expuestas

#### Tools (acciones)

```
pulso_contexto(scope?: string) → SessionBriefing
```
Retorna el resumen de prioridades para la sesión actual. Incluye: top-5 quickwins (impact≥4, effort∈{XS,S}), bloqueadores activos (status=bloqueado), último ítem tocado por scope, hilos en stage=en-desarrollo. Este es el tool que Claude Code llama al inicio de cada sesión.

```
pulso_buscar(q: string, scope?: string, tipo?: string, limit?: int=10) → Item[]
```
Búsqueda full-text sobre el índice GIN existente (v0002). Retorna título, summary, status, effort_ai, impact_ai, scope.

```
pulso_crear(
  title: string,
  summary: string,
  type: enum[bug|feature|tech-debt|infra|docs|ops|seguridad|producto|idea],
  scope_name: string,
  effort_ai?: enum[XS|S|M|L|XL],
  impact_ai?: int[1-5],
  origen: string = "claude-code"
) → Item
```
Crea un ítem. Si `effort_ai` / `impact_ai` no se pasan, el worker de enriquecimiento los llena después (F2). `scope_name` puede ser existente o nuevo — se crea el scope si no existe.

```
pulso_completar(
  item_id: uuid | search_query: string,
  nota?: string,
  commit_sha?: string
) → Item
```
Marca un ítem como `hecho`. Si se pasa `search_query` en vez de `item_id`, busca el ítem más relevante por texto. `nota` y `commit_sha` quedan en `source_refs` del ítem para trazabilidad.

```
pulso_listar(
  scope?: string,
  status?: string[],
  tipo?: string,
  stage?: string,
  quickwins?: bool,
  limit?: int=20
) → Item[]
```
Lista filtrada. `quickwins=true` aplica el filtro `impact_ai>=4 AND effort_ai IN ('XS','S')`.

```
pulso_hilo_crear(
  title: string,
  summary: string,
  scope_name: string
) → Thread
```
Crea un Hilo nuevo en stage `idea`. El Hilo es el contenedor para el funnel investigación→spec→dev.

```
pulso_hilo_avanzar(
  thread_id: uuid,
  artifact?: { stage: string, content: string }
) → Thread
```
Avanza el Hilo al siguiente stage. Si se pasa `artifact`, guarda el contenido (markdown) como artifact del stage actual antes de avanzar. Ejemplo: antes de pasar de `investigación` a `historias`, se guarda el resumen de investigación.

```
pulso_hilo_listar(stage?: string, scope?: string) → Thread[]
```
Lista Hilos con su stage actual y último artifact.

#### Prompts (inyección de contexto)

```
pulso://briefing
```
Un Prompt MCP (no un tool) que Claude Code puede incluir en su contexto al inicio de sesión. Retorna markdown estructurado: estado del backlog, hilos activos, bugs de prod recientes, próximos pasos sugeridos. Se registra en `claude_desktop_config.json` como prompt.

```
pulso://decision/{topic}
```
Busca en el log de decisiones (ítems con `type=decisión` o items marcados como `decision_log=true`) sobre el topic. Retorna el razonamiento registrado.

#### Resources (lectura pasiva)

```
pulso://scope/{scope_name}
```
Vista completa de un scope: ítems por status, hilos activos, velocidad última semana.

### A.3 Configuración del cliente (Claude Code)

El usuario agrega en `~/.claude/claude_desktop_config.json`:

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

El API token se genera desde la pantalla Admin de Pulso (ya existe el sistema de ApiToken).

### A.4 Integración en CLAUDE.md del repo efrain

Se agrega una sección al CLAUDE.md de efrain:

```markdown
## Pulso — conducto pre/post sesión

Al inicio de sesión:
1. Llamar `pulso_contexto()` para obtener prioridades y bloqueadores del día.
2. Si hay hilos en stage `en-desarrollo`, revisar cuál corresponde a la tarea en curso.

Al cierre de sesión (tras push/merge exitoso):
1. Llamar `pulso_completar(item_id_o_query, nota="<descripción breve>", commit_sha="<sha>")` 
   para cada ítem resuelto en esta sesión.
2. Si se descubrió deuda técnica nueva, crear ítems con `pulso_crear(...)`.
3. Si se inició una feature nueva, crear un Hilo con `pulso_hilo_crear(...)`.
```

---

## Parte B: Hilos de desarrollo (Thread funnel)

### B.1 Modelo de datos

```sql
-- thread stages: idea → investigación → historias → spec → en-desarrollo → review → hecho
CREATE TABLE threads (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scope_id UUID NOT NULL REFERENCES scopes(id),
  title TEXT NOT NULL,
  summary_md TEXT,
  stage TEXT NOT NULL DEFAULT 'idea'
    CHECK (stage IN ('idea','investigación','historias','spec','en-desarrollo','review','hecho','descartado')),
  assignee_user_id UUID REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE thread_artifacts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  thread_id UUID NOT NULL REFERENCES threads(id),
  stage TEXT NOT NULL,  -- qué stage produjo este artifact
  kind TEXT NOT NULL CHECK (kind IN ('investigación','historias','spec','notas','decisión')),
  content_md TEXT NOT NULL,
  created_by_user_id UUID REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Un thread puede tener muchos items asociados (los que se crean durante el dev)
ALTER TABLE items ADD COLUMN thread_id UUID REFERENCES threads(id);
```

### B.2 Stages y transiciones

```
idea ──→ investigación ──→ historias ──→ spec ──→ en-desarrollo ──→ review ──→ hecho
  ↓           ↓               ↓           ↓            ↓              ↓
descartado  descartado     descartado  descartado   descartado    descartado
```

**idea**: existe el problema, título y summary breve.

**investigación**: se agrega un artifact con notas de contexto — qué sistemas existentes hacen algo similar, qué código en el repo es relevante, qué decisiones pasadas afectan. Claude Code puede generar esto con `pulso_hilo_avanzar(id, {stage: "investigación", content: "..."})`.

**historias**: artifact con user stories en formato `Como [rol], quiero [qué], para [por qué]`. Puede generarse con AI desde el artifact de investigación.

**spec**: artifact con la spec técnica completa (equivalente a los docs/superpowers/specs/ actuales). Idealmente generado con AI desde investigación + historias.

**en-desarrollo**: los ítems de implementación se crean y se linkean al hilo con `thread_id`. El hilo sabe que hay trabajo activo.

**review**: code review / QA / eyeball del owner. El hilo puede tener un artifact con checklist de review.

**hecho**: todos los ítems linkeados están en status `hecho`. El hilo se archiva automáticamente si todos sus ítems están completos.

### B.3 UI en Pulso

Una vista nueva `/hilos` (lista tipo Kanban por stage) y `/hilos/{id}` (detalle con artifacts por stage, lista de ítems linkeados). La vista de detalle permite avanzar el stage con un botón y agregar artifacts manualmente o vía AI.

No se reemplaza la vista de backlog — los Hilos son una capa de organización encima, no un reemplazo.

### B.4 Generación de spec AI (bonus)

Un endpoint `POST /api/v1/threads/{id}/elaborate-stage` que:
1. Toma el stage actual y los artifacts existentes
2. Llama a Claude (Haiku para historias, Sonnet para spec) con el contexto del hilo
3. Guarda el resultado como un artifact draft del siguiente stage
4. El usuario puede editarlo y luego confirmar el avance de stage

Esto hace el funnel semiautomático sin ser completamente autónomo.

---

## Parte C: Sentry webhook (spec breve, implementación posterior)

`POST /api/v1/webhooks/sentry` — recibe alertas de Sentry.

Lógica:
1. Si el error ya tiene un ítem en Pulso (por fingerprint): bump `stale_risk=true`, agrega ocurrencia en `source_refs`.
2. Si es nuevo: crea ítem `type=bug`, `origen=sentry`, con title = error message, summary = stack trace truncado + URL Sentry, `scope_name` derivado de la transaction path.
3. Haiku triage asíncrono: rellena `impact_ai` (basado en affected users del evento Sentry), `effort_ai`, `impact_rationale` con hipótesis de root cause.
4. `pulso_contexto()` siempre incluye los últimos 3 bugs de Sentry sin ítem asociado (por si el webhook falló).

---

## Parte D: Git webhook → progreso automático

`POST /api/v1/webhooks/github` — recibe push/PR merge events.

Lógica:
1. Para cada commit, extraer scope del conventional commit prefix (`fix(auth):` → scope `auth`).
2. Marcar ítems del scope como "recientemente tocados" (campo `last_touched_at`, sin cambiar status).
3. Si el commit message incluye `pulso:UUID` o `closes pulso:UUID` → marcar el ítem como hecho con el SHA del commit.
4. PR merged → buscar en el body del PR ítems referenciados y marcarlos.

Este webhook es opcional y no bloquea el flujo — es enriquecimiento pasivo.

---

## Alcance de implementación (qué queda fuera del sprint)

- Decision log queryable: se implementa como un tipo de ítem `type=decisión` + el tool `pulso_buscar` ya funciona. No requiere endpoints nuevos.
- Velocidad / métricas: pantalla futura, datos ya se acumulan.
- Public roadmap: lectura only, pantalla futura.
- Autonomous fix agent: post-F3, requiere validar el workflow primero.
- Multi-usuario / asignación de stages: la columna `assignee_user_id` ya está en el schema pero la UI/lógica es post-MVP.

---

## Migraciones Alembic

| Versión | Descripción |
|---------|-------------|
| v0003 | Tabla `threads` + `thread_artifacts` |
| v0004 | `items.thread_id FK → threads` |

---

## Checklist de done

- [ ] `POST /mcp` implementado con protocolo JSON-RPC 2.0 + SSE
- [ ] 8 tools funcionando (contexto, buscar, crear, completar, listar, hilo_crear, hilo_avanzar, hilo_listar)
- [ ] 2 prompts (briefing, decision) + 1 resource (scope)
- [ ] Auth via Bearer token (ApiToken existente)
- [ ] Tests: 1 test por tool (mock del db), 1 test de auth (token inválido → 401)
- [ ] `claude_desktop_config.json` ejemplo documentado en README del repo
- [ ] Sección en CLAUDE.md del repo efrain con protocolo pre/post sesión
- [ ] Migraciones v0003 + v0004 con downgrade
- [ ] UI `/hilos` (lista) + `/hilos/{id}` (detalle con stages + artifacts)
- [ ] Endpoint `POST /api/v1/threads/{id}/elaborate-stage` con AI (Haiku para historias, Sonnet para spec)
- [ ] Sentry webhook (básico, sin AI triage por ahora)
- [ ] Documentación del formato `pulso:UUID` en commit messages
