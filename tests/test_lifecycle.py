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
            "idea", "backlog", "spec", "in-progress",
            "blocked", "in-review", "done", "discarded",
        }


def test_valid_forward_transitions():
    assert valid_transition("backlog", "in-progress")
    assert valid_transition("in-progress", "in-review")
    assert valid_transition("in-review", "done")
    assert valid_transition("idea", "backlog")


def test_invalid_transitions_rejected():
    assert not valid_transition("idea", "done")
    assert not valid_transition("done", "in-progress")
    assert not valid_transition("backlog", "in-review")


def test_same_status_is_idempotent():
    assert valid_transition("in-progress", "in-progress")


def test_reopen_from_terminal_only_to_backlog():
    assert valid_transition("done", "backlog")
    assert valid_transition("discarded", "backlog")
    assert not valid_transition("done", "in-progress")
    assert not valid_transition("discarded", "done")


def test_non_terminal_targets_excludes_done_discarded():
    targets = non_terminal_targets("in-progress")
    assert "done" not in targets
    assert "discarded" not in targets
    assert "in-review" in targets


def test_allowed_targets_stable_order():
    targets = allowed_targets("backlog")
    # El orden sigue el orden canónico de STATUSES.
    assert targets == [s for s in STATUSES if s in set(targets)]


def test_terminal_set():
    assert TERMINAL == frozenset({"done", "discarded"})
