"""Pure-logic tests for the Gantt geometry (no DB) — the dynamic axis + rollups.

Runnable locally without Postgres: `python -m pytest tests/test_management_gantt.py`.
"""

from datetime import date

from app.management import gantt


def test_axis_weekly_then_monthly_boundary():
    # A plan crossing week 12 must switch from S1..S12 (weekly) to M4.. (monthly).
    start = date(2026, 1, 5)  # a Monday
    end = date(2026, 6, 30)
    axis = gantt.build_axis(start, end)
    cols = axis["columns"]
    weeks = [c for c in cols if c["kind"] == "week"]
    months = [c for c in cols if c["kind"] == "month"]
    assert len(weeks) == 12, "first 12 columns must be weekly"
    assert weeks[0]["label"] == "S1"
    assert weeks[11]["label"] == "S12"
    assert months, "plan beyond week 12 must have monthly columns"
    assert months[0]["label"] == "M4", "monthly labels continue at M4 (per template)"
    # weekly columns are contiguous 7-day blocks aligned to Monday
    assert weeks[0]["start"] == date(2026, 1, 5)
    assert weeks[1]["start"] == date(2026, 1, 12)


def test_axis_short_plan_is_all_weekly():
    start = date(2026, 3, 2)
    end = date(2026, 3, 20)  # ~3 weeks
    axis = gantt.build_axis(start, end)
    assert all(c["kind"] == "week" for c in axis["columns"])
    assert axis["n"] == 3


def test_axis_groups_have_colspans_summing_to_n():
    axis = gantt.build_axis(date(2026, 1, 5), date(2026, 8, 31))
    assert sum(g["span"] for g in axis["groups"]) == axis["n"]


def test_bar_geometry_spans_expected_columns():
    axis = gantt.build_axis(date(2026, 1, 5), date(2026, 3, 30))
    cols = axis["columns"]
    # a task covering the first two weeks starts at col 0, spans 2
    geo = gantt.bar_geometry(date(2026, 1, 6), date(2026, 1, 15), cols)
    assert geo == (0, 2)
    # an undated task has no bar
    assert gantt.bar_geometry(None, None, cols) is None
    # a task entirely before the axis is off-chart
    assert gantt.bar_geometry(date(2025, 1, 1), date(2025, 1, 2), cols) is None


def test_phase_rollup_from_children():
    # Phase has no own dates; its bar rolls up min(child start)..max(child end).
    tasks = [
        {"id": "p", "parent_id": None, "name": "Phase", "start": None, "end": None,
         "progress": 0, "is_milestone": False, "deps": None, "sort_order": 0},
        {"id": "a", "parent_id": "p", "name": "Task A", "start": date(2026, 1, 5),
         "end": date(2026, 1, 11), "progress": 50, "is_milestone": False, "deps": None,
         "sort_order": 0},
        {"id": "b", "parent_id": "p", "name": "Task B", "start": date(2026, 1, 12),
         "end": date(2026, 1, 25), "progress": 0, "is_milestone": False, "deps": None,
         "sort_order": 1},
    ]
    s, e = gantt.plan_bounds(tasks)
    assert s == date(2026, 1, 5) and e == date(2026, 1, 25)
    axis = gantt.build_axis(s, e)
    rows = gantt.plan_rows(tasks, axis["columns"])
    assert [r["name"] for r in rows] == ["Phase", "Task A", "Task B"]
    assert rows[0]["level"] == 1 and rows[1]["level"] == 2
    # the phase bar spans the union of its children (3 weekly columns)
    assert rows[0]["bar"] == (0, 3)


def test_milestone_is_a_point_on_the_axis():
    axis = gantt.build_axis(date(2026, 1, 5), date(2026, 2, 28))
    cols = axis["columns"]
    tasks = [
        {"id": "m", "parent_id": None, "name": "Go-live", "start": date(2026, 1, 20),
         "end": None, "progress": 0, "is_milestone": True, "deps": None, "sort_order": 0},
    ]
    rows = gantt.plan_rows(tasks, cols)
    assert rows[0]["bar"] is None
    assert rows[0]["milestone_idx"] == gantt.milestone_index(date(2026, 1, 20), cols)
    assert rows[0]["milestone_idx"] is not None


def test_today_fraction_bounds():
    axis = gantt.build_axis(date(2026, 1, 5), date(2026, 3, 1))
    cols = axis["columns"]
    assert gantt.today_fraction(date(2020, 1, 1), cols) is None  # before
    frac = gantt.today_fraction(date(2026, 1, 19), cols)
    assert frac is not None and 0.0 <= frac <= 1.0
