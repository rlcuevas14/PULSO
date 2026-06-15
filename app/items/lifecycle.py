"""Máquina de estados del ciclo de vida de un ítem.

Única fuente de verdad de las TRANSICIONES válidas (la matriz `TRANSITIONS`), consumida
por UI, REST y MCP. El dominio de estados vive en `app.enums` (ITEM_STATUSES / TERMINAL);
aquí se reexporta con los nombres históricos `STATUSES` y `TERMINAL` para no romper imports.
Los 8 estados del enum real de items.status:
    idea, backlog, spec, en-curso, bloqueado, en-revision, hecho, descartado
"""

from app.enums import ITEM_STATUSES as STATUSES  # alias histórico (tupla, ordenada)
from app.enums import TERMINAL as _ENUM_TERMINAL

# Estados terminales: el cierre pasa por POST /close (pide motivo), no por PATCH directo.
# Se expone como frozenset (contrato histórico) a partir del dominio de app.enums.
TERMINAL: frozenset[str] = frozenset(_ENUM_TERMINAL)

# Matriz de transiciones válidas: origen -> destinos permitidos.
TRANSITIONS: dict[str, frozenset[str]] = {
    "idea": frozenset({"backlog", "spec", "en-curso", "descartado"}),
    "backlog": frozenset({"spec", "en-curso", "bloqueado", "hecho", "descartado"}),
    "spec": frozenset({"backlog", "en-curso", "bloqueado", "descartado"}),
    "en-curso": frozenset({"backlog", "bloqueado", "en-revision", "hecho", "descartado"}),
    "bloqueado": frozenset({"backlog", "en-curso", "descartado"}),
    "en-revision": frozenset({"en-curso", "bloqueado", "hecho", "descartado"}),
    # Estados terminales: solo "Reabrir" -> backlog.
    "hecho": frozenset({"backlog"}),
    "descartado": frozenset({"backlog"}),
}


def valid_transition(from_status: str, to_status: str) -> bool:
    """True si la transición from_status -> to_status es válida."""
    if from_status == to_status:
        return True  # idempotente
    return to_status in TRANSITIONS.get(from_status, frozenset())


def allowed_targets(from_status: str) -> list[str]:
    """Destinos permitidos desde el estado dado (orden estable para la UI)."""
    targets = TRANSITIONS.get(from_status, frozenset())
    return [s for s in STATUSES if s in targets]


def non_terminal_targets(from_status: str) -> list[str]:
    """Destinos permitidos que NO son terminales (los que van por PATCH; el resto por /close)."""
    return [s for s in allowed_targets(from_status) if s not in TERMINAL]
