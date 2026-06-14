"""Endpoint MCP-over-HTTP (Streamable HTTP, modo JSON) para Pulso.

Implementa el subconjunto del protocolo MCP 2025-03-26 que Claude Code usa sobre HTTP
request/response (sin SSE): initialize, tools/list, tools/call, prompts, resources.
Auth obligatoria por Bearer (ApiToken); las tools de escritura exigen scope 'write'.
"""

import json
from typing import Any, Callable

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import ApiToken
from app.auth.service import verify_api_token
from app.database import get_db
from app.mcp import tools

PROTOCOL_VERSION = "2025-03-26"
_ALLOWED_ORIGINS = {"https://pulso.eduk3.cl"}


class Tool:
    def __init__(self, name: str, description: str, schema: dict, handler: Callable, write: bool):
        self.name = name
        self.description = description
        self.schema = schema
        self.handler = handler
        self.write = write


def _scope_obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


_STR = {"type": "string"}
_INT = {"type": "integer"}

TOOLS: dict[str, Tool] = {
    "pulso_contexto": Tool(
        "pulso_contexto",
        "Resumen de prioridades para iniciar una sesión: quickwins, bloqueadores, bugs de "
        "Sentry sin ítem, hilos activos, vecindad del grafo y (si hay embeddings) ítems "
        "semánticamente cercanos a work_description.",
        _scope_obj({"scope": _STR, "work_description": _STR}, []),
        tools.pulso_contexto, write=False,
    ),
    "pulso_buscar": Tool(
        "pulso_buscar", "Búsqueda full-text de ítems del backlog.",
        _scope_obj({"q": _STR, "scope": _STR, "tipo": _STR, "limit": _INT}, ["q"]),
        tools.pulso_buscar, write=False,
    ),
    "pulso_listar": Tool(
        "pulso_listar",
        "Lista filtrada de ítems. order: impacto|prioridad|topologico. quickwins: bool.",
        _scope_obj({"scope": _STR, "status": {"type": "array", "items": _STR},
                    "tipo": _STR, "order": _STR, "quickwins": {"type": "boolean"},
                    "limit": _INT}, []),
        tools.pulso_listar, write=False,
    ),
    "pulso_scopes": Tool(
        "pulso_scopes",
        "Lista los scopes (agrupadores del backlog) con nombre, descripción, conteo de ítems "
        "y ejemplos. ÚSALO antes de crear un ítem para elegir el scope correcto y no duplicar.",
        _scope_obj({}, []),
        tools.pulso_scopes, write=False,
    ),
    "pulso_mover_scope": Tool(
        "pulso_mover_scope",
        "Mueve un ítem a otro scope existente (corrige categorización). "
        "Acepta item_id o query de texto.",
        _scope_obj({"item_id": _STR, "query": _STR, "scope_name": _STR}, ["scope_name"]),
        tools.pulso_mover_scope, write=True,
    ),
    "pulso_crear": Tool(
        "pulso_crear",
        "Crea un ítem en el backlog (status backlog, origen ia-sesion por defecto). "
        "Crea el scope si no existe.",
        _scope_obj({"title": _STR, "summary": _STR, "type": _STR, "scope_name": _STR,
                    "effort_ai": _STR, "impact_ai": _INT, "origen": _STR},
                   ["title", "type", "scope_name"]),
        tools.pulso_crear, write=True,
    ),
    "pulso_avanzar": Tool(
        "pulso_avanzar",
        "Cambia el estado de un ítem (transición validada; terminales van por pulso_completar). "
        "Acepta item_id o query de texto.",
        _scope_obj({"item_id": _STR, "query": _STR, "to_status": _STR}, ["to_status"]),
        tools.pulso_avanzar, write=True,
    ),
    "pulso_completar": Tool(
        "pulso_completar",
        "Marca un ítem como hecho (con nota y commit_sha opcionales). Reporta ítems "
        "desbloqueados. Acepta item_id o search_query (aborta si es ambiguo).",
        _scope_obj({"item_id": _STR, "search_query": _STR, "nota": _STR, "commit_sha": _STR}, []),
        tools.pulso_completar, write=True,
    ),
    "pulso_relacionar": Tool(
        "pulso_relacionar",
        "Crea un arco del grafo entre dos ítems. relation: blocks|requires|conflicts|related|part_of. "
        "Acepta ids o queries de texto.",
        _scope_obj({"source_id": _STR, "source_query": _STR, "target_id": _STR,
                    "target_query": _STR, "relation": _STR, "note": _STR}, ["relation"]),
        tools.pulso_relacionar, write=True,
    ),
    "pulso_hilo_crear": Tool(
        "pulso_hilo_crear", "Crea un Hilo (feature pesada) en stage idea.",
        _scope_obj({"title": _STR, "summary": _STR, "scope_name": _STR}, ["title", "scope_name"]),
        tools.pulso_hilo_crear, write=True,
    ),
    "pulso_hilo_avanzar": Tool(
        "pulso_hilo_avanzar",
        "Avanza un Hilo al siguiente stage; opcionalmente guarda un artefacto "
        "{stage, content} del stage actual.",
        _scope_obj({"thread_id": _STR, "artifact": {"type": "object"}}, ["thread_id"]),
        tools.pulso_hilo_avanzar, write=True,
    ),
    "pulso_hilo_listar": Tool(
        "pulso_hilo_listar", "Lista Hilos (filtro opcional por stage y scope).",
        _scope_obj({"stage": _STR, "scope": _STR}, []),
        tools.pulso_hilo_listar, write=False,
    ),
    "pulso_incidentes": Tool(
        "pulso_incidentes",
        "Lista los errores de Sentry del contenedor de incidentes. status: new|linked|"
        "resolved|ignored|todos (default new).",
        _scope_obj({"status": _STR, "limit": _INT}, []),
        tools.pulso_incidentes, write=False,
    ),
    "pulso_incidente": Tool(
        "pulso_incidente",
        "Detalle de un incidente CON stack trace (excepción, archivo:línea, código) traído "
        "de Sentry — lo que necesitas para localizar y arreglar el error. id = id del incidente.",
        _scope_obj({"id": _STR}, ["id"]),
        tools.pulso_incidente, write=False,
    ),
    "pulso_incidente_resolver": Tool(
        "pulso_incidente_resolver",
        "Marca un incidente como resuelto en Pulso y (por defecto) en Sentry. Úsalo tras "
        "arreglar el bug. resolver_en_sentry: bool (default true).",
        _scope_obj({"id": _STR, "nota": _STR, "commit_sha": _STR,
                    "resolver_en_sentry": {"type": "boolean"}}, ["id"]),
        tools.pulso_incidente_resolver, write=True,
    ),
}

PROMPTS = {
    "briefing": {
        "name": "briefing",
        "description": "Contexto de inicio de sesión (prioridades, bloqueadores, vecindad).",
        "arguments": [{"name": "scope", "description": "scope activo", "required": False},
                      {"name": "work_description", "description": "qué vas a trabajar", "required": False}],
    },
    "decision": {
        "name": "decision",
        "description": "Decisiones de arquitectura registradas (item_comments kind=decision).",
        "arguments": [{"name": "topic", "description": "tema a buscar", "required": True}],
    },
}

RESOURCE_TEMPLATES = [
    {"uriTemplate": "pulso://scope/{scope_name}", "name": "scope",
     "description": "Vista de un scope: ítems por estado.", "mimeType": "application/json"},
    {"uriTemplate": "pulso://graph/{item_id}", "name": "graph",
     "description": "Subgrafo de relaciones de un ítem.", "mimeType": "application/json"},
]


def _err(rpc_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


def _ok(rpc_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _tool_result(payload: Any, is_error: bool = False) -> dict:
    text_out = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text_out}], "isError": is_error}


async def _dispatch(msg: dict, token: ApiToken, db: AsyncSession) -> dict | None:
    method = msg.get("method")
    rpc_id = msg.get("id")
    params = msg.get("params") or {}

    # Notificaciones (sin id) no llevan respuesta.
    if rpc_id is None:
        return None

    if method == "initialize":
        return _ok(rpc_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}, "prompts": {}, "resources": {}},
            "serverInfo": {"name": "pulso", "version": "1.0"},
            "instructions": "Backlog de Eduk3. Llama pulso_contexto al inicio y "
                            "pulso_completar al cierre de cada sesión.",
        })

    if method == "ping":
        return _ok(rpc_id, {})

    if method == "tools/list":
        return _ok(rpc_id, {"tools": [
            {"name": t.name, "description": t.description, "inputSchema": t.schema}
            for t in TOOLS.values()
        ]})

    if method == "tools/call":
        name = params.get("name") or ""
        arguments = params.get("arguments") or {}
        tool = TOOLS.get(name)
        if tool is None:
            return _ok(rpc_id, _tool_result(f"Tool desconocida: {name}", is_error=True))
        if tool.write and token.scopes != "write":
            return _ok(rpc_id, _tool_result(
                f"El tool '{name}' requiere scope 'write'; tu token es '{token.scopes}'.",
                is_error=True))
        try:
            result = await tool.handler(db, token, arguments)
            await db.commit()
            return _ok(rpc_id, _tool_result(result))
        except tools.ToolError as e:
            await db.rollback()
            return _ok(rpc_id, _tool_result(str(e), is_error=True))
        except KeyError as e:
            await db.rollback()
            return _ok(rpc_id, _tool_result(f"Falta argumento requerido: {e}", is_error=True))

    if method == "prompts/list":
        return _ok(rpc_id, {"prompts": list(PROMPTS.values())})

    if method == "prompts/get":
        return await _prompt_get(rpc_id, params, token, db)

    if method == "resources/list":
        return _ok(rpc_id, {"resources": []})

    if method == "resources/templates/list":
        return _ok(rpc_id, {"resourceTemplates": RESOURCE_TEMPLATES})

    if method == "resources/read":
        return await _resource_read(rpc_id, params, db)

    return _err(rpc_id, -32601, f"Método no soportado: {method}")


async def _prompt_get(rpc_id: Any, params: dict, token: ApiToken, db: AsyncSession) -> dict:
    name = params.get("name")
    args = params.get("arguments") or {}
    if name == "briefing":
        ctx = await tools.pulso_contexto(db, token, args)
        body = json.dumps(ctx, ensure_ascii=False, indent=2)
        text_out = f"Contexto de Pulso para la sesión:\n{body}"
    elif name == "decision":
        topic = args.get("topic", "")
        rows = (await db.execute(text("""
            SELECT c.body_md, c.author, i.title
            FROM item_comments c JOIN items i ON i.id = c.item_id
            WHERE c.kind = 'decision' AND c.body_md ILIKE :t
            ORDER BY c.created_at DESC LIMIT 10
        """), {"t": f"%{topic}%"})).mappings().all()
        if rows:
            text_out = "Decisiones registradas:\n" + "\n".join(
                f"- ({r['title']}, {r['author']}) {r['body_md']}" for r in rows)
        else:
            text_out = f"No hay decisiones registradas sobre «{topic}»."
    else:
        return _err(rpc_id, -32602, f"Prompt desconocido: {name}")
    return _ok(rpc_id, {"messages": [{"role": "user", "content": {"type": "text", "text": text_out}}]})


async def _resource_read(rpc_id: Any, params: dict, db: AsyncSession) -> dict:
    uri = params.get("uri", "")
    payload: Any
    if uri.startswith("pulso://scope/"):
        name = uri.split("/", 3)[-1]
        rows = (await db.execute(text("""
            SELECT i.status, count(*) AS n FROM items i JOIN scopes s ON s.id = i.scope_id
            WHERE s.name = :name GROUP BY i.status
        """), {"name": name})).mappings().all()
        payload = {"scope": name, "counts": {r["status"]: r["n"] for r in rows}}
    elif uri.startswith("pulso://graph/"):
        import uuid as _uuid
        item_id = uri.split("/", 3)[-1]
        from app.items import graph
        payload = await graph.subgraph(db, _uuid.UUID(item_id))
    else:
        return _err(rpc_id, -32602, f"URI no soportada: {uri}")
    return _ok(rpc_id, {"contents": [
        {"uri": uri, "mimeType": "application/json", "text": json.dumps(payload, ensure_ascii=False)}
    ]})


def _origin_ok(request: Request) -> bool:
    origin = request.headers.get("origin")
    return origin is None or origin in _ALLOWED_ORIGINS


def mount_mcp(app: FastAPI) -> None:
    @app.post("/mcp")
    async def mcp_post(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
        if not _origin_ok(request):
            return JSONResponse({"error": "origin no permitido"}, status_code=403)

        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return JSONResponse({"error": "Bearer token requerido"}, status_code=401)
        token = await verify_api_token(db, auth.split(" ", 1)[1].strip())
        if token is None:
            return JSONResponse({"error": "token inválido o revocado"}, status_code=401)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(_err(None, -32700, "JSON inválido"), status_code=400)

        if isinstance(body, list):
            responses = [r for m in body if (r := await _dispatch(m, token, db)) is not None]
            return JSONResponse(responses) if responses else Response(status_code=202)

        response = await _dispatch(body, token, db)
        if response is None:
            return Response(status_code=202)
        return JSONResponse(response)

    @app.get("/mcp")
    async def mcp_get() -> Response:
        # Modo stateless sin streams server→cliente: el transporte responde 405 al GET.
        return Response(status_code=405)
