from app.items.lifecycle import (
    STATUSES,
    TERMINAL,
    allowed_targets,
    non_terminal_targets,
    valid_transition,
)


def test_all_statuses_have_transition_entry():
    # Todos los estados son origen válido en la matriz.
    for s in STATUSES:
        assert s in {
            "idea", "backlog", "spec", "en-curso",
            "bloqueado", "en-revision", "hecho", "descartado",
        }


def test_valid_forward_transitions():
    assert valid_transition("backlog", "en-curso")
    assert valid_transition("en-curso", "en-revision")
    assert valid_transition("en-revision", "hecho")
    assert valid_transition("idea", "backlog")


def test_invalid_transitions_rejected():
    assert not valid_transition("idea", "hecho")
    assert not valid_transition("hecho", "en-curso")
    assert not valid_transition("backlog", "en-revision")


def test_same_status_is_idempotent():
    assert valid_transition("en-curso", "en-curso")


def test_reopen_from_terminal_only_to_backlog():
    assert valid_transition("hecho", "backlog")
    assert valid_transition("descartado", "backlog")
    assert not valid_transition("hecho", "en-curso")
    assert not valid_transition("descartado", "hecho")


def test_non_terminal_targets_excludes_hecho_descartado():
    targets = non_terminal_targets("en-curso")
    assert "hecho" not in targets
    assert "descartado" not in targets
    assert "en-revision" in targets


def test_allowed_targets_stable_order():
    targets = allowed_targets("backlog")
    # El orden sigue el orden canónico de STATUSES.
    assert targets == [s for s in STATUSES if s in set(targets)]


def test_terminal_set():
    assert TERMINAL == frozenset({"hecho", "descartado"})
