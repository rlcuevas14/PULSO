# Conectar Claude Code a Pulso (MCP-over-HTTP)

Pulso expone un endpoint MCP en `https://pulso.tidanalytics.com/mcp` (Streamable HTTP, modo JSON).
Cualquier instancia de Claude Code se conecta con solo un token — sin instalar nada local.

## 1. Generar un token

Entra a `https://pulso.tidanalytics.com/admin` → **Generar token MCP** (scope `write` para poder
crear/cerrar ítems desde la sesión). Copia el token (se muestra una sola vez).

## 2. Registrar el server en Claude Code

**Opción A — comando (escribe en `~/.claude.json`, scope local):**
```bash
claude mcp add --transport http pulso https://pulso.tidanalytics.com/mcp \
  --header "Authorization: Bearer <TU_TOKEN>"
```

**Opción B — `.mcp.json` versionado en la raíz del repo (compartido con el equipo):**
```json
{
  "mcpServers": {
    "pulso": {
      "type": "http",
      "url": "https://pulso.tidanalytics.com/mcp",
      "headers": { "Authorization": "Bearer ${PULSO_TOKEN}" }
    }
  }
}
```
Claude Code expande `${PULSO_TOKEN}` desde el entorno (no dejes el token en git).
Verifica con `claude mcp list`.

## 3. Tools disponibles

| Tool | Scope | Para qué |
|------|-------|----------|
| `pulso_contexto(scope?, work_description?)` | read | Prioridades de inicio de sesión (3 capas: local + vecindad + semántica) |
| `pulso_buscar(q, …)` | read | Búsqueda full-text |
| `pulso_listar(scope?, status?, order?, …)` | read | Lista filtrada (order: impacto/prioridad/topologico) |
| `pulso_crear(title, type, scope_name, …)` | write | Crear ítem (origen ia-sesion) |
| `pulso_avanzar(item_id|query, to_status)` | write | Cambiar estado (validado) |
| `pulso_completar(item_id|search_query, nota?, commit_sha?)` | write | Marcar hecho + reportar desbloqueados |
| `pulso_relacionar(source, target, relation, note?)` | write | Crear arco del grafo |

Prompts: `briefing`, `decision`. Resource templates: `pulso://scope/{name}`, `pulso://graph/{item_id}`.

## 4. Protocolo (pre/post sesión)

Ver la sección "Pulso — conducto pre/post sesión" en el `CLAUDE.md` del repo efrain.
