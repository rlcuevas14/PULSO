# Spec — Rediseño del Backlog + panel Archive (Registro)

**Fecha**: 2026-07-06 · **Estado**: aprobado en dirección, pendiente de plan de implementación
**Decisiones del owner**: Archive con entrada propia en NAV · el tablero excluye `done`/`discarded` · Registro agrupado por semana · URLs bookmarkeables como "vistas guardadas" (sin storage) · `blocked` se mantiene como estado.

## Objetivo

Convertir `/backlog` de una lista plana con ruido (los cerrados se mezclan, sin búsqueda, una sola vista) en el panel de trabajo diario del flujo humano+agente, y dar a lo ya hecho un registro histórico propio ("qué se cerró cuándo y junto a qué") sin migraciones de esquema.

Los 8 estados actuales (`idea, backlog, spec, in-progress, blocked, in-review, done, discarded`) **no cambian**: mapean 1:1 al pipeline agéntico de consenso (`idea → spec [gate humano] → agent-ready → in-progress [agente] → in-review [revisión humana] → done`, con carril rápido que salta `spec`). El trabajo es exponer lo que ya existe, no rediseñar el modelo.

## Alcance por iteración

| Iteración | Contenido | Esquema |
|---|---|---|
| **1** | Solo-abiertos por defecto · búsqueda FTS · filtros priority/effort + chips rápidos · fix bug topológico · vista Tablero · cerrar desde fila/tarjeta · chip Ready | Sin migraciones |
| **2** | Panel `/registro` (Archive) · lista agrupada (group-by) · orden en SQL · resumen semanal IA | Sin migraciones |
| **3** (bosquejo) | Vistas guardadas con nombre · drag & drop · command palette · ciclos reales | Requiere storage |

---

## Iteración 1 — Backlog operable

### 1.1 Solo abiertos por defecto (param `show`)

- Nuevo query param `show` ∈ `open` (default) | `all` | `closed` en `GET /backlog`.
  - `open` → `Item.status.in_(_OPEN)` (constante existente en `app/ui/router.py`, ya usada por `/prioridad`).
  - `closed` → `Item.status.in_(("done", "discarded"))`.
- Si el param `status` (select existente) viene con valor, **manda sobre `show`** (elegir un estado concreto es más específico).
- UI: grupo segmentado de 3 chips `[Abiertos | Cerrados | Todos]` al inicio de la toolbar de filtros, mismo cableado que el resto (`hx-get="/backlog" hx-target="#items-view" hx-include="#filters" hx-push-url="true"`).
- El contador del header (`{{ items|length }} ítems`) refleja el resultado filtrado, como hoy.

### 1.2 Búsqueda de texto (param `q`)

- Input de texto en la toolbar con `hx-trigger="keyup changed delay:300ms, search"`, dentro del form `#filters`.
- Backend: si `q` no vacío → `search_items(db, q, project_id=pid)` (FTS existente: `plainto_tsquery('spanish')` + `ts_rank` sobre `search_vector`, índice GIN) para obtener ids rankeados; luego la query de items filtra `Item.id.in_(ids)` y aplica el resto de filtros.
- Orden con `q` presente: default "relevance" (orden del rank devuelto); los demás órdenes siguen seleccionables.
- 0 resultados → empty state con link "Limpiar filtros".
- Limitación aceptada v1: `plainto_tsquery` hace AND de términos (búsqueda estricta). Mejora futura: `websearch_to_tsquery`.

### 1.3 Filtros nuevos + chips rápidos

- Selects nuevos en toolbar: `priority` (p0..p3) y `effort` (XS..XL), single-value como los existentes, filtro SQL directo.
- Chips rápidos (params bool, estilados como toggles con `.p-pill` + accent):
  - `quickwins` — `impact_ai >= 4 AND effort_ai IN ('XS','S')` (misma banda que `service._apply_item_filters`).
  - `agent_ready` — columna `agent_ready` (SQL).
  - `urgent` — `priority IN ('p0','p1')`.
  - `stale` y `graph_blocked` — ya existen como checkboxes; se re-estilan como chips del mismo grupo.
- Botón "Limpiar" → `hx-get="/backlog"` sin params.
- Todos los filtros persisten en URL (`hx-push-url`): las "vistas guardadas" de esta fase son URLs bookmarkeables (decisión del owner).

### 1.4 Fix de ordenamiento (bug real, verificado)

- `app/items/service.py:253`: `if order == "topologico"` computa el `topo_rank`, pero `_order_items` (línea 180) lo aplica solo con `order == "topological"`. **Ningún valor activa ambas ramas**: el orden topológico de `list_items` nunca funciona — afecta MCP `pulso_list` y REST. Fix: `"topologico"` → `"topological"` + test unitario.
- La UI **no** tiene este bug (su `_order_items` acepta ambos alias y el default `"prioridad"` funciona). Normalizar el default de la ruta a `"priority"` por higiene, manteniendo los alias en español para URLs viejas.

### 1.5 Vista Tablero (param `view`)

- Param `view` ∈ `list` (default) | `board`, con toggle segmentado `[Lista | Tablero]` en la toolbar, persistido en URL.
- El target de swap `#items-table` se renombra a `#items-view`; la ruta renderiza `partials/items_table.html` o el nuevo `partials/items_board.html` según `view` (mismo mecanismo HX-Request actual).
- Columnas: los 6 estados abiertos, en orden de funnel con `blocked` al final: `idea, backlog, spec, in-progress, in-review, blocked`. **`done`/`discarded` nunca son columnas** (decisión del owner: lo hecho vive en Archive). Con `view=board` los filtros de estado terminal se ignoran.
- Layout: patrón canónico de `hilos.html` — `flex gap-3 overflow-x-auto snap-x snap-mandatory`, columnas `snap-start shrink-0 w-64 bg-surface-soft border border-hairline rounded-2xl p-2`, header con badge de estado + count.
- Tarjeta (`.p-card`): type pill, título → `/items/{id}`, iconos ⛔/⚠/🤖 (mismas condiciones que la fila de lista), select de prioridad (`_priority_select.html`, ya parametrizable), select "mover" (mismo POST a `/ui/items/{id}/transition`, patrón 204+HX-Refresh) y botón ✓ de cierre (§1.6).
- **Sin drag & drop en esta iteración**: mover vía select reutiliza toda la infraestructura validada por la matriz de `lifecycle.py`. DnD es iteración 3 (exige respuestas parciales en lugar de full refresh).
- Colores de columna: mapa canónico de `_status_badge.html` — copiar, no importar (convención existente).

### 1.6 Cerrar desde fila/tarjeta

- Endpoint nuevo `GET /ui/items/{id}/close-modal` → renderiza `partials/_close_modal.html` (extraído del modal actual de `item_detail.html`, parametrizado por item). Guard: `_guard_row` lectura (el POST de cierre ya valida escritura).
- En fila (lista) y tarjeta (tablero): botón ✓ con `hx-get` a ese endpoint, target `#modal-slot` (div vacío al final de `backlog.html`), y apertura del modal tras el swap (`hx-on::after-swap`).
- El form del partial usa `hx-post="/ui/items/{id}/close"` (regla obligatoria: handlers 204+HX-Refresh van con `hx-post`). Radio done/discarded según targets terminales válidos del estado actual; `done` solo visible desde `backlog`, `in-progress`, `in-review` (matriz de `lifecycle.py`).
- `item_detail.html` migra a incluir el mismo partial (dedupe; render estático con contexto, sin el endpoint).
- El botón ✓ solo se muestra en estados no terminales.

### 1.7 Chip "Ready" computado

- En la ruta: `ready_ids = {items con agent_ready AND status IN ('backlog','spec') AND id NOT IN blocked_ids}` (los `blocked_ids` ya se computan en cada request).
- Chip 🤖 en fila y tarjeta. Es la "definition of ready" visible: especificado + marcado agent-ready + sin bloqueos abiertos en el grafo.
- Nota: el filtro `agent_ready` (§1.3) filtra por la columna SQL; el chip visual muestra readiness completa. Diferencia deliberada y documentada en el template.

---

## Iteración 2 — Archive (Registro) + agrupación

### 2.1 Panel `/registro` (NAV: "Archive")

- NAV: añadir `("/registro", "Archive")` al array de `base.html:8` (labels del NAV en inglés como los existentes; ruta en español como `/prioridad`, `/hilos`).
- Ruta `GET /registro`: items del proyecto con `status IN ('done','discarded')`, `ORDER BY closed_at DESC`. Ventana inicial: últimas 12 semanas con contenido; "Cargar más" con `hx-get` + param `before=<fecha>`.
- **Agrupación por semana ISO de `closed_at`** (groupby en Python post-fetch). Header por grupo: "Semana del {lunes} – {domingo}" + pills `N done` / `M discarded`.
- Por ítem: día de cierre, título → `/items/{id}`, type pill, badge done/discarded (estilos existentes de `_status_badge.html`), scope, **motivo** y **commit**:
  - Motivo: `payload.reason` del `ItemEvent` con `action="closed"` — un solo fetch batch `IN (ids)` de los eventos de los ítems listados (sin N+1). Si un ítem tiene varios (reopen/re-close), usar el más reciente.
  - Commit: `source_refs.commit_sha` (donde `close_item` lo mergea); sha corto linkeado a `{project.repo_url}/commit/{sha}` si `repo_url` está definido.
- Ítems terminales sin `closed_at` (legacy pre-v0004): grupo "Sin fecha" al final.
- Filtros mínimos: `scope`, `item_type`, `q` (reusa `search_items` restringido a terminales).
- Solo lectura: reabrir se hace desde el detalle (ya existe `/ui/items/{id}/reopen`).
- Home: sexta `.p-homecard` "Archive" en `dashboard.html` con count de cerrados de la semana en curso (el grid 3-col queda en 2 filas completas).

### 2.2 Lista agrupada (param `group`)

- Param `group` ∈ `none` (default) | `scope` | `type` | `priority` | `status`, solo aplicable con `view=list`; select "Agrupar por" en la toolbar.
- Render: `<details open>` nativo por grupo (patrón ya usado en la casa), header con nombre + count usando los mapas de color canónicos. El orden activo se mantiene dentro de cada grupo (groupby en Python post-orden).

### 2.3 Orden en SQL

- Mover `priority` (CASE p0..p3, NULL al final → luego `impact_ai DESC`), `impact` y `recent` a `ORDER BY` en SQL, para que el `LIMIT 300` corte sobre el orden correcto y no sobre inserción. `topological` permanece en Python (necesita el grafo).
- El cap 300 se mantiene; si tras esto sigue doliendo, "Cargar más" con offset (medir antes de construir).

### 2.4 Resumen semanal IA (degradable)

- Botón "Resumen IA" por grupo de semana en `/registro` → `hx-get /ui/registro/summary?week=YYYY-Www` → partial colapsable con resumen markdown.
- Backend: función nueva `summarize_closed(items+reasons)` en `app/ai/llm.py` (Haiku, mockable como el resto). Sin almacenamiento — se regenera on-demand (barato). Sin `ANTHROPIC_API_KEY` → mensaje de no disponible, nunca error.

---

## Iteración 3 — Futuro (bosquejo, no diseñar aún)

- **Vistas guardadas con nombre**: requiere storage (tabla de preferencias o `localStorage`); hoy no existe ningún mecanismo de preferencias por usuario.
- **Drag & drop en tablero**: SortableJS (CDN) + respuestas parciales por columna en `/ui/items/{id}/transition` en lugar de 204+HX-Refresh.
- **Command palette (Cmd+K)**: navegación + acciones sobre ítem seleccionado.
- **Ciclos reales** (tabla `cycles` con planificación): solo si el registro derivado por semana queda corto.

## Fuera de alcance

- Cambios al enum `status` (`blocked` se mantiene — decisión del owner; sigue siendo candidato a deprecación futura a favor del blocking derivado del grafo).
- Cambios a herramientas MCP (`pulso_list`/`pulso_search` sin cambios de contrato; el fix §1.4 los corrige de rebote).
- Renombrar thread stages (siguen en español, fuera de alcance según CLAUDE.md).
- Paginación completa del backlog.

## Datos y migraciones

**Cero migraciones en iteraciones 1–2.** Todo deriva de columnas existentes: `closed_at`, `item_events` (motivo), `source_refs` (commit), `agent_ready`, `stale_risk`, `search_vector`, `scope_id`, y de la constante `_OPEN`.

## Convenciones a respetar

- Design system: tokens y clases `.p-*` de `partials/_head.html`; nada de grises/azules hardcodeados; sin modificadores de opacidad sobre tokens semánticos.
- Handlers 204+HX-Refresh → siempre `hx-post` en el form.
- Mapas de color status/type: copiar en cada template, no importar (convención marcada en el código).
- Copy de UI: las pantallas existentes usan copy en español ("Todos los scopes", "Cerrar ítem") pese a que CLAUDE.md dice inglés — los textos nuevos siguen lo shipped (español), labels de NAV en inglés como el array actual. Si se quiere unificar, es tarea aparte.
- `flash_success` para feedback de cierre (el handler `/close` ya lo emite, con celebración en `done`).
- Toda mutación emite `ItemEvent` (no hay mutaciones nuevas: se reutilizan `transition` y `close`).

## Testing (cada feature trae tests; CI es el gate)

- `/backlog`: `show=open` default excluye terminales; `q` usa FTS (el `search_vector` está parcheado globalmente en `conftest.py`); filtros `priority`/`effort`/`urgent`/`quickwins`/`agent_ready`; `view=board` renderiza 6 columnas sin `done`; interacción `status` > `show`.
- `service.list_items(order="topological")` ordena topológicamente (test del fix §1.4).
- `/registro`: agrupación por semana ISO, mapeo motivo/commit desde events (batch, ítem re-cerrado usa el evento más reciente), grupo "Sin fecha", "Cargar más".
- `GET /ui/items/{id}/close-modal`: 200 con targets terminales correctos por estado; 404 cross-project vía `_guard_row`.
- Local: `ruff check app/ tests/` + `mypy app/` + pytest del área contra `pulso_test` (reset de schema si hay sospecha de DB sucia).

## Riesgos

1. **FTS estricto** (`plainto_tsquery` = AND de términos): aceptado v1; upgrade a `websearch_to_tsquery` si molesta.
2. **Tablero con columnas largas** si hay muchos ítems por estado: mitigado por `show=open` + filtros compartidos con la vista lista; el cap 300 aplica antes de columnar.
3. **Full refresh por movimiento en tablero** (204+HX-Refresh): aceptable sin DnD; se revisita en iteración 3.
4. **Motivos ausentes** en ítems cerrados antes de que `close_item` exigiera razón: se muestra "—", no error.
