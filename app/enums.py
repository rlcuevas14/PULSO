"""Única fuente de verdad de los dominios cerrados (enums) del sistema.

Cada tupla refleja EXACTAMENTE los valores de los CHECK constraints vigentes en la
base de datos (declarados en los modelos y en las migraciones v0001/v0003/v0005).
Cambiar un valor aquí implica una migración de schema — no es una edición cosmética.

Este módulo es base: NO importa nada de `app.*` para no introducir ciclos.
"""

# --- items ---
ITEM_TYPES: tuple[str, ...] = (
    "bug",
    "feature",
    "tech-debt",
    "infra",
    "docs",
    "ops",
    "seguridad",
    "producto",
    "idea",
)
ITEM_STATUSES: tuple[str, ...] = (
    "idea",
    "backlog",
    "spec",
    "en-curso",
    "bloqueado",
    "en-revision",
    "hecho",
    "descartado",
)
# Estados terminales: el cierre pasa por POST /close (pide motivo), no por PATCH.
TERMINAL: tuple[str, ...] = ("hecho", "descartado")
# Estados abiertos: todos los no terminales (orden estable, derivado de ITEM_STATUSES).
OPEN_STATUSES: tuple[str, ...] = tuple(s for s in ITEM_STATUSES if s not in TERMINAL)
PRIORITIES: tuple[str, ...] = ("p0", "p1", "p2", "p3")
EFFORTS: tuple[str, ...] = ("XS", "S", "M", "L", "XL")
ORIGENES: tuple[str, ...] = ("digest", "humano", "ia-sesion", "sentry", "agente")

# --- grafo ---
RELATIONS: tuple[str, ...] = ("blocks", "requires", "conflicts", "related", "part_of")

# --- comentarios de ítem ---
COMMENT_KINDS: tuple[str, ...] = ("comentario", "analisis-ia", "decision", "cambio-estado")

# --- hilos de desarrollo ---
THREAD_STAGES: tuple[str, ...] = (
    "idea",
    "investigacion",
    "historias",
    "spec",
    "en-desarrollo",
    "review",
    "hecho",
    "descartado",
)
THREAD_ARTIFACT_KINDS: tuple[str, ...] = (
    "investigacion",
    "historias",
    "spec",
    "notas",
    "decision",
)

# --- órdenes de listado (UI / MCP); no es un CHECK, pero es dominio cerrado ---
LIST_ORDERS: tuple[str, ...] = ("impacto", "prioridad", "topologico", "reciente")

# --- jobs / agentes ---
AGENT_RUN_KINDS: tuple[str, ...] = (
    "enrich",
    "dedup",
    "triage-sentry",
    "digest-email",
    "fix-externo",
)
AGENT_RUN_STATUSES: tuple[str, ...] = ("pendiente", "corriendo", "ok", "error")

# --- sentry ---
SENTRY_LEVELS: tuple[str, ...] = ("error", "warning", "info")
SENTRY_TRIAGE: tuple[str, ...] = ("pendiente", "bug-real", "input-malo", "3rd-party", "ruido")
SENTRY_STATUSES: tuple[str, ...] = ("new", "linked", "resolved", "ignored")

# --- auth ---
USER_ROLES: tuple[str, ...] = ("admin", "viewer")
TOKEN_SCOPES: tuple[str, ...] = ("read", "write")


def sql_list(values: tuple[str, ...] | list[str]) -> str:
    """Lista de literales SQL separados por coma, p.ej. "'a','b'", para usar en `IN (...)`.

    `repr` de un str da comillas simples, que es el delimitador de cadena válido en SQL.
    """
    return ",".join(repr(v) for v in values)


def check_in(col: str, values: tuple[str, ...] | list[str]) -> str:
    """Expresión SQL `col IN ('a','b',...)` para un CheckConstraint."""
    return f"{col} IN ({sql_list(values)})"
