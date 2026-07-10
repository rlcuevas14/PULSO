# Spec — Pestaña "Management": PMO ligera agent-native (Entregables · Pendientes · Plan/Gantt)

**Fecha**: 2026-07-10 · **Estado**: diseño aprobado, pendiente de plan de implementación
**Rol objetivo**: consultor / líder de proyectos (solo-preneur) que gestiona N proyectos y necesita, *además* del backlog técnico, las herramientas ejecutivas clásicas de una PMO — pero manejadas por Claude vía MCP.

**Decisiones del owner** (cerradas en brainstorming 2026-07-10):
- Bytes de entregables en **`bytea` de Postgres** (no volumen, no object-store) — ceiling documentado abajo.
- Pendientes = **entidad nueva ligera**, NO se extienden los `items` (el backlog dev queda limpio).
- Gantt = **`PlanTask` liviano** renderizado como **HTML/CSS**; **toda edición vía MCP** (UI de Gantt es solo-lectura). Sin critical-path ni resource-leveling.
- **Responsable = texto libre** (no FK a `users`): un consultor nombra clientes/externos que no son usuarios de Pulso.
- **Compartimientos = lista plana** por proyecto (sin carpetas anidadas v1).
- El Gantt sigue el template `Gantt Generator VARAJO.md` (jerarquía 3 niveles, resolución dinámica semanas→meses, barras sólidas, hitos ◆, dependencias ↳, clases agnósticas para estilizar). Colores/tipografía se alinean a los tokens de Pulso.

---

## Objetivo

Abrir un **dominio nuevo** en Pulso — la gestión clásica de proyecto — sin tocar el backlog. El backlog dev es *una parte* de un proyecto; "Management" cubre el resto que un líder de proyecto necesita para demostrar valor y control: **entregables/documentación**, **pendientes con responsable y estado**, y **planificación (Gantt)**.

Principio rector, heredado del ADN agent-native de Pulso: **la UI es un visor; el editor es Claude Code vía MCP.** Cada funcionalidad expone tools MCP para que Claude la lea y escriba como banco de memoria; el solo-preneur también opera por UI donde tiene sentido (subir un archivo, marcar un pendiente), pero el Gantt se edita **solo** por MCP (decisión del owner). Esto elimina el 80% del frontend: no hay editores drag-drop, ni formularios complejos, ni librería JS de Gantt.

## No-objetivos (fuera de scope, explícito)

- Critical path, nivelación de recursos, forecasting de capacidad (territorio MS Project — no es el usuario).
- Dibujo de flechas de dependencia en el Gantt v1 (se guarda el dato `deps`; se renderiza marca sutil ↳, no arcos).
- Carpetas anidadas / árbol de compartimientos.
- `assignee` como FK a `users` (responsable es texto).
- Preview inline de Office (docx/xlsx/pptx) — se ofrece descarga + metadata; preview inline solo md/html/pdf.
- Extracción/OCR de texto de binarios; búsqueda semántica sobre entregables (v1 = búsqueda por nombre+resumen).
- Portfolio rollups cross-proyecto; colaboración en tiempo real.

---

## Arquitectura — un módulo, una pestaña

**Módulo nuevo `app/management/`** (cohesivo, no tres módulos):

```
app/management/
  __init__.py
  models.py     # Compartment, Deliverable, DeliverableVersion, Pending, PlanTask, ManagementEvent
  service.py    # mutaciones validadas + emisión de ManagementEvent (audit)
  router.py     # /management/{subtab} (UI) + /ui/management/... (acciones HTMX) + descarga/upload
  gantt.py      # cálculo del eje temporal (resolución dinámica) + árbol de tareas para el render
```

MCP: implementaciones en `app/mcp/tools.py`, registro en `app/mcp/server.py` (reusa transporte, auth, scope `write`, y el **failsafe `token.project_id`** existente).

**Aislamiento**: todas las queries filtran por `project_id`. UI/REST pasan por el chokepoint `projects/access.py` (`resolve_current_project` / `require_project_access`); MCP filtra por `token.project_id`. Cross-proyecto imposible, igual que el resto de Pulso.

**Navegación**: entrada nueva `Management` en el navbar (per-proyecto). `/management` → 302 a la subtab por defecto (`/management/pendientes`). Subtabs por segmento de path: `/management/pendientes`, `/management/entregables`, `/management/plan`. Nav de subtabs con el patrón pill `category-tab` / `category-tab-active` ya existente en `_head.html`.

---

## Modelo de datos (migración `v0013`)

Todas las tablas: `project_id` **NOT NULL** FK `projects(id) ON DELETE CASCADE` (sin datos legacy que backfillar, a diferencia de `items`). `created_at`/`updated_at` con `server_default=func.now()`. UUID PKs.

### `management_events` — primitiva de auditoría del dominio

El equivalente genérico de `ItemEvent` para Management (append-only; **toda mutación emite uno** — convención Pulso).

| Columna | Tipo | Nota |
|---|---|---|
| `id` | UUID PK | |
| `project_id` | UUID NOT NULL FK | aislamiento |
| `entity_type` | String(20) | `deliverable` \| `compartment` \| `pending` \| `plan_task` |
| `entity_id` | UUID | id de la entidad afectada |
| `actor` | String(255) | usuario o token/agente |
| `action` | String(60) | `created`, `updated`, `version_added`, `completed`, `removed`, `moved`... |
| `payload` | JSONB null | diff/contexto |
| `created_at` | TIMESTAMPTZ | |

### `compartments` — "compartimientos" (lista plana)

| Columna | Tipo | Nota |
|---|---|---|
| `id` | UUID PK | |
| `project_id` | UUID NOT NULL FK | |
| `name` | String(120) NOT NULL | único por proyecto: `UNIQUE(project_id, name)` |
| `description` | Text null | |
| `sort_order` | SmallInt default 0 | orden manual en el directorio |
| `created_by`, `created_at`, `updated_at` | | |

### `deliverables` — identidad lógica del entregable

La fila es la **identidad** (nombre + compartimiento + tipo); el contenido vive en versiones. Re-subir = nueva versión, nunca sobrescribe.

| Columna | Tipo | Nota |
|---|---|---|
| `id` | UUID PK | |
| `project_id` | UUID NOT NULL FK | |
| `compartment_id` | UUID NOT NULL FK `compartments(id) ON DELETE RESTRICT` | |
| `name` | String(200) NOT NULL | nombre visible (ej. "Propuesta comercial v2") |
| `doc_type` | String(10) NOT NULL | whitelist: `docx, pdf, html, md, xlsx, pptx` (CHECK) |
| `status` | String(15) NOT NULL default `draft` | `draft, review, final, archived` (CHECK) |
| `owner` | String(255) null | responsable (texto libre) |
| `summary_md` | Text null | resumen que Claude escribe al subir — usado para búsqueda + preview rápido |
| `current_version` | SmallInt NOT NULL default 1 | apunta al `version_no` vigente |
| `created_by`, `created_at`, `updated_at` | | |

`UNIQUE(compartment_id, name)` — un nombre por compartimiento.

### `deliverable_versions` — contenido versionado (append-only)

| Columna | Tipo | Nota |
|---|---|---|
| `id` | UUID PK | |
| `deliverable_id` | UUID NOT NULL FK `... ON DELETE CASCADE` | |
| `version_no` | SmallInt NOT NULL | 1,2,3...; `UNIQUE(deliverable_id, version_no)` |
| `content` | **BYTEA** NOT NULL | bytes del archivo |
| `mime` | String(120) NOT NULL | validado contra `doc_type` |
| `size_bytes` | Integer NOT NULL | |
| `sha256` | String(64) NOT NULL | integridad + dedup (no re-versiona si idéntico al vigente) |
| `note` | Text null | qué cambió (lo pone Claude/usuario) |
| `created_by`, `created_at` | | |

**Rollback** = crear una nueva versión copiando el `content` de una anterior (append-only, nunca se muta ni borra histórico). Ceiling `bytea` (ponytail): infla `pg_dump` si los archivos crecen mucho → migrar a volumen/object-store cuando el tamaño medio pase de ~pocos MB o el volumen total moleste al backup. Límite duro de subida: **10 MB/archivo** (validado en upload y en MCP `put`).

### `pendings` — pendientes con responsable y estado

| Columna | Tipo | Nota |
|---|---|---|
| `id` | UUID PK | |
| `project_id` | UUID NOT NULL FK | |
| `title` | String(300) NOT NULL | verbo-sustantivo ("Enviar propuesta al cliente") |
| `detail_md` | Text null | |
| `owner` | String(255) null | responsable (texto libre) |
| `status` | String(12) NOT NULL default `open` | `open, doing, blocked, done` (CHECK) |
| `due_date` | Date null | `<input type="date">` nativo, sin lib |
| `plan_task_id` | UUID null FK `plan_tasks(id) ON DELETE SET NULL` | enlace opcional pendiente↔Gantt |
| `created_by`, `created_at`, `updated_at`, `closed_at` | | `closed_at` al pasar a `done` |

### `plan_tasks` — filas del Gantt (jerarquía 3 niveles)

| Columna | Tipo | Nota |
|---|---|---|
| `id` | UUID PK | |
| `project_id` | UUID NOT NULL FK | |
| `parent_id` | UUID null FK `plan_tasks(id) ON DELETE CASCADE` | autoref; nivel = profundidad (1=Fase, 2=Sub-fase, 3=Tarea) |
| `name` | String(200) NOT NULL | |
| `start_date` | Date null | null en fases → se deriva de hijos (rollup) |
| `end_date` | Date null | idem |
| `progress` | SmallInt NOT NULL default 0 | 0-100 (CHECK) |
| `is_milestone` | Boolean NOT NULL default false | ◆ en `start_date`; `end_date` se ignora |
| `deps` | JSONB null | lista de `plan_task` ids que preceden (dato; render ↳ sutil) |
| `sort_order` | SmallInt NOT NULL default 0 | orden entre hermanos |
| `created_by`, `created_at`, `updated_at` | | |

Regla de profundidad: no se *fuerza* en BD (evita rigidez), pero el render y las guías tratan 3 niveles; `service` valida que `parent` no cree ciclos y que no exceda profundidad 3 (rechaza con error claro por MCP).

---

## MCP — 9 tools nuevos (17 → 26)

Convención Pulso: una tool por verbo, English, write tools exigen scope `write` (si no → `isError`), todas filtran por `token.project_id` (failsafe). **Recordatorio del CLAUDE.md**: los tools nuevos solo aparecen tras **REINICIAR Claude Code**.

**Entregables (3)**
- `pulso_deliverable_list` *(read)* — filtra por `compartment`/`status`/`q` (nombre+resumen). Devuelve **metadata only** (id, name, compartment, doc_type, status, owner, current_version, size, summary) — nunca bytes (no contamina el contexto).
- `pulso_deliverable_get` *(read)* — metadata + `summary_md` + historial de versiones. `include_content=true` (opt-in) devuelve el contenido: texto inline para `md`/`html`; base64 para binarios, con guard de tamaño (rechaza > límite y sugiere descarga por UI).
- `pulso_deliverable_put` *(write)* — crea entregable o **agrega versión** (por `name`+`compartment`). Auto-crea el compartimiento si no existe (evita un tool aparte). Content como texto o base64; valida whitelist `doc_type`↔`mime` y tamaño; dedup por `sha256` (no versiona si idéntico). Acepta `summary_md`, `status`, `owner`, `note`. Emite `version_added`.

**Pendientes (3)**
- `pulso_pending_list` *(read)* — filtros `status`/`owner`/`overdue`/`plan_task_id`; orden por due/estado.
- `pulso_pending_upsert` *(write)* — crea/edita (title, detail, owner, status, due_date, plan_task_id).
- `pulso_pending_complete` *(write)* — atajo a `status=done` + `closed_at` (espeja `pulso_complete`).

**Plan/Gantt (3)**
- `pulso_plan_get` *(read)* — árbol completo de `plan_tasks` (jerarquía + fechas + progreso + hitos + deps) + eje temporal resuelto. Es el "estado del plan" que Claude lee antes de editar.
- `pulso_plan_task_upsert` *(write)* — crea/edita una tarea (name, parent, start, end, progress, is_milestone, deps, sort_order). Valida ciclos/profundidad.
- `pulso_plan_task_remove` *(write)* — borra tarea (CASCADE a hijas); emite `removed` con payload del subárbol.

*(Opcional, no v1)*: prompt MCP `status_report` que genere un status ejecutivo del proyecto cruzando pendientes + hitos del plan + últimas versiones de entregables. Reusa `app/ai/llm.py`. Anotado como oportunidad, fuera de v1.

---

## Subtab 1 — Pendientes

Gestor de acciones con responsable y estado. UI **editable** (el solo-preneur crea/marca; Claude también por MCP).

**Vista** (mejores prácticas de list-UX): tabla/lista con columnas Título · Responsable · Estado · Vence. Reusa la toolbar del backlog:
- **Group-by** (`status` default | `owner` | `due`) — patrón group-by ya existente en el backlog redesign.
- **Chips rápidos** (`.p-pill` + accent): `Abiertos` (status≠done), `Vencidos` (`due_date < today AND status≠done`), `Míos` (owner = usuario actual). Combinables, persisten en URL (`hx-push-url`).
- **Filtros**: `status`, `owner`. Orden: por `due_date` asc (nulls last), luego estado.
- **Vencidos** resaltados con token `warning`/`error` (nunca hardcodear color — tokens de `_head.html`).
- Empty state con link "Limpiar filtros".

**Acciones** (patrón HTMX de Pulso, forms `hx-post`, respuestas `204 + HX-Refresh` → refresco; regla hx-post-on-204):
- Crear/editar: modal en `#modal-slot` (patrón `_close_modal.html`). Campos: title, detail_md, owner (input texto), status (select), due (`<input type="date">` nativo), plan_task_id (select opcional de tareas del plan).
- Completar: botón ✓ → `pending_complete`. Éxito vía `flash_success`.
- Toda mutación por `service` → emite `ManagementEvent`.

## Subtab 2 — Entregables

Mini-directorio navegable de documentación. UI **editable** (subir/organizar; Claude también por MCP).

**Layout**: columna izquierda = lista de **compartimientos** (plana, `sort_order`); panel derecho = entregables del compartimiento seleccionado, como tarjetas `.p-card` con **icono por tipo** (docx/pdf/xlsx/pptx/md/html), nombre, `status` badge, owner, versión vigente, fecha, tamaño.

**Metadata > carpetas** (best practice DMS): además del compartimiento, cada entregable lleva owner/status/tipo/versión/fecha filtrables. Búsqueda `q` por nombre+`summary_md` (ILIKE v1; FTS con `search_vector` generado = mejora futura, misma infra que items).

**Preview por tipo** (seguridad primero):
- `md` → render markdown con el filtro **sanitizado** que Pulso ya usa (reusar; nunca inyectar HTML crudo).
- `html` → **`<iframe sandbox>` sin `allow-scripts`** + CSP restrictiva. Contenido propio/agente pero se trata como no-confiable (convención Pulso: sanitizar contra XSS).
- `pdf` → embed nativo (`<embed>`/`<iframe>` del blob) — sin lib.
- `docx`/`xlsx`/`pptx` → **sin preview inline v1**: icono + metadata + botón Descargar.

**Subida** (respaldo humano, además de MCP): form `multipart/form-data` (`<input type="file">` nativo, drag-drop opcional con dropzone HTML) → valida whitelist extensión/MIME + tamaño (10 MB) → crea versión. Éxito `flash_success`.

**Descarga**: `GET /management/deliverables/{id}/download?v=` — stream del `bytea` con `Content-Disposition` y MIME correcto; guard de acceso por proyecto. Default = versión vigente.

**Versionado**: la tarjeta muestra "v{n}"; panel de detalle lista versiones (fecha, autor, nota, tamaño) con Descargar y **Rollback** (crea nueva versión = copia de la elegida). Diff/compare = fuera de v1.

## Subtab 3 — Plan (Gantt HTML, solo-lectura; edición 100% por MCP)

Render server-side HTML/CSS del árbol `plan_tasks`, siguiendo el template `Gantt Generator VARAJO.md`. **Sin JS de Gantt.** Cualquier ajuste → el usuario le pide a Claude, que edita por `pulso_plan_task_upsert`.

**Estructura de filas (3 niveles)** — indentación + jerarquía tipográfica por clases agnósticas alineadas a tokens Pulso:
- Nivel 1 (Fase): fila destacada, barra **resumen** (rollup) que abarca de `min(start)` de sus descendientes a `max(end)`. Clase `phase-level-1`.
- Nivel 2 (Sub-fase): indentación simple. `phase-level-2`.
- Nivel 3 (Tarea): indentación doble. `phase-level-3`.

**Eje temporal de resolución dinámica** (el "zoom" incorporado — no requiere pan/zoom JS):
- Semanas para las primeras **12 semanas** desde el inicio del proyecto (`min(start_date)`): `S1..S12`.
- A partir de S13, columnas agrupadas por **mes**: `Mes 4, Mes 5...`.
- Cabecera de **dos niveles**: superior = Mes global; inferior = detalle (semana o mes). Cálculo en `gantt.py`.

**Barras** (best practice: comprensión en 10 s, paleta limitada):
- Bloques sólidos coloreados estructuralmente (divs posicionados en grid CSS), no caracteres "X". Relleno de `progress %` como porción más oscura de la barra.
- **Color por fase de nivel 1** (3-6 colores máx; paleta derivada del accent per-proyecto + tokens). No un color por barra.
- Celdas vacías totalmente limpias (sin ruido en la grilla).
- Línea vertical **"hoy"** sobre la grilla.

**Elementos de valor**:
- **Hitos** ◆ (rombo) en `is_milestone`, coloreados con token de énfasis; se ubican donde se concentra el riesgo (go-live, cutover).
- **Dependencias**: marca sutil `↳` al inicio de la barra cuando `deps` no vacío (arcos = futuro).

**Clases agnósticas** (del template, para estilizar sin tocar el render): `phase-level-1/2/3`, `timeline-block`, `milestone`, `progress-fill`, `today-line`, `dep-marker`. Definidas como `.p-*` en `_head.html` (tokens/variables, `darkMode:'class'`, accent per-proyecto — nunca hex inline).

**Bonus gratis**: al ser HTML, "Imprimir → PDF" del navegador produce un Gantt ejecutivo presentable (CSS `@media print`). Sin export dedicado v1.

---

## Fiabilidad, calidad y seguridad (transversal)

- **Auditoría**: toda mutación (UI/REST/MCP) pasa por `service` y emite `ManagementEvent` (append-only). Espeja la garantía de `ItemEvent`.
- **Aislamiento**: `project_id` NOT NULL en todo; UI/REST por `access.py`, MCP por `token.project_id` failsafe. Tests de cross-proyecto (no debe ver/escribir otro proyecto).
- **Seguridad de contenido**: whitelist estricta `doc_type`↔`mime`+extensión; límite 10 MB; `sha256` para integridad/dedup; HTML en `<iframe sandbox>` sin scripts + CSP; markdown sanitizado (reusar filtro Pulso). El upload es un boundary de confianza → validar siempre.
- **Integridad de datos**: versiones append-only (nunca se pierde histórico ante re-subida); rollback = nueva versión, no mutación.
- **Degradación IA**: `summary_md` lo puede escribir Claude, pero es opcional (nullable) — la funcionalidad no depende de tener API key (convención `app/ai/llm.py`).
- **i18n**: ninguna string de UI hardcodeada. Nuevas claves `management.*` en `en`/`es`/`fr` (source = `en`). `tests/test_i18n.py` exige paridad de catálogos + placeholders + cobertura de templates. Grupos de claves: `management.nav`, `management.pending.*`, `management.deliverable.*`, `management.plan.*`, labels de enum (`deliverable_status.*`, `pending_status.*`).

## Tests (cada feature trae tests — CI es el gate real)

Correr con `TEST_DATABASE_URL` + `DEBUG=true` (cookie segura rompe tests UI); resetear schema si dirty.

- **Modelo/constraints**: CHECKs de enums; unicidades (`compartment name` por proyecto, `deliverable name` por compartimiento, `version_no` por deliverable).
- **Service/audit**: cada mutación emite exactamente un `ManagementEvent` con `entity_type`/`action` correctos.
- **Aislamiento**: MCP con token del proyecto A no ve/edita entidades del proyecto B; write tools sin scope `write` → `isError`.
- **Entregables**: whitelist rechaza tipo/MIME no permitido; > 10 MB rechazado; re-subida idéntica (mismo sha256) no crea versión; distinta sí incrementa `version_no`; rollback crea versión nueva = copia; descarga devuelve MIME+bytes correctos.
- **Pendientes**: transición a `done` setea `closed_at`; chip `overdue` = `due_date<today AND status≠done`.
- **Plan/Gantt** (lógica no trivial → tests obligados):
  - `gantt.py` **resolución del eje**: frontera exacta S12→Mes 4 (proyecto que cruza semana 12); cabecera de dos niveles correcta.
  - **Rollup** de fase nivel-1 = `min(start)`/`max(end)` de descendientes.
  - Validación de **ciclos** y **profundidad > 3** en `plan_task_upsert` → error claro.
- **UI**: subtabs renderizan; upload happy-path; Gantt renderiza clases esperadas; preview HTML va en iframe sandbox (assert del atributo).

## Migración

`v0013` (head actual = `v0012`): crea `management_events, compartments, deliverables, deliverable_versions, pendings, plan_tasks` con sus FKs `project_id` CASCADE, uniques y CHECKs. Sin backfill (dominio nuevo). Enums en `app/enums.py` (`DELIVERABLE_TYPES`, `DELIVERABLE_STATUSES`, `PENDING_STATUSES`, `MANAGEMENT_ENTITY_TYPES`).

---

## Plan por iteraciones

Orden recomendado por **derisking** (rebana verticalmente para validar el esqueleto del módulo + patrón MCP + audit + i18n + tests en lo más simple, antes de lo más pesado). El Gantt es el feature estrella; puede adelantarse si se prioriza demo — el orden es flexible, los tres están specados por completo.

| Iter | Contenido | Por qué aquí |
|---|---|---|
| **1** | Esqueleto módulo + nav "Management" + subtabs + **Pendientes** completo (modelo, MCP ×3, UI CRUD, audit, i18n, tests) + migración `v0013` con las 6 tablas | Slice vertical mínimo que ejercita todo el andamiaje (módulo, MCP, `ManagementEvent`, aislamiento, i18n, patrón HTMX). Bajo riesgo. |
| **2** | **Entregables** (compartimientos, deliverables+versiones, upload multipart, descarga stream, preview md/html/pdf sandbox, MCP ×3, rollback) | Storage + seguridad (XSS/whitelist/tamaño) — el bloque más sensible, ya con el esqueleto probado. |
| **3** | **Plan/Gantt** (plan_tasks, `gantt.py` eje dinámico + rollup, render HTML/CSS 3 niveles + hitos + deps + hoy + print, MCP ×3) | Render más complejo; se apoya en todo lo anterior. |
| **4** (bosquejo) | Búsqueda FTS en entregables (search_vector generado); prompt MCP `status_report` IA; enlaces pendiente↔entregable; export/print pulido | Mejoras sobre base sólida. |

## Preguntas abiertas (no bloquean el plan)

1. **Subtab por defecto** de `/management`: propongo `pendientes` (lo más usado día a día). ¿O prefieres `plan`?
2. **Orden de iteraciones**: ¿ok derisking (pendientes→entregables→gantt) o quieres el **Gantt primero** por valor de demo?
3. **Prioridad en pendientes**: v1 los dejé sin campo `priority` (status+due bastan para un consultor). ¿Agregar un `priority` simple (alta/media/baja) o mantener lean?
