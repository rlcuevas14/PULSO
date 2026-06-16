"""Item lifecycle state machine.

Single source of truth for valid transitions, consumed by UI, REST, and MCP.
States: idea, backlog, spec, in-progress, blocked, in-review, done, discarded
"""

from app.enums import ITEM_STATUSES as STATUSES
from app.enums import TERMINAL as _ENUM_TERMINAL

TERMINAL: frozenset[str] = frozenset(_ENUM_TERMINAL)

TRANSITIONS: dict[str, frozenset[str]] = {
    "idea":        frozenset({"backlog", "spec", "in-progress", "discarded"}),
    "backlog":     frozenset({"spec", "in-progress", "blocked", "done", "discarded"}),
    "spec":        frozenset({"backlog", "in-progress", "blocked", "discarded"}),
    "in-progress": frozenset({"backlog", "blocked", "in-review", "done", "discarded"}),
    "blocked":     frozenset({"backlog", "in-progress", "discarded"}),
    "in-review":   frozenset({"in-progress", "blocked", "done", "discarded"}),
    "done":        frozenset({"backlog"}),
    "discarded":   frozenset({"backlog"}),
}


def valid_transition(from_status: str, to_status: str) -> bool:
    if from_status == to_status:
        return True
    return to_status in TRANSITIONS.get(from_status, frozenset())


def allowed_targets(from_status: str) -> list[str]:
    targets = TRANSITIONS.get(from_status, frozenset())
    return [s for s in STATUSES if s in targets]


def non_terminal_targets(from_status: str) -> list[str]:
    return [s for s in allowed_targets(from_status) if s not in TERMINAL]
