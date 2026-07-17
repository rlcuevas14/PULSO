"""Pure Gantt geometry — no DB, no ORM, no `now()`. Unit-testable in isolation.

Faithful to the original Gantt template: a dynamic-resolution time axis (first 12 weeks
are weekly columns `S1..S12`, then monthly columns `M4, M5, ...`), a two-level header
(top = calendar period, bottom = column label), solid bars positioned across columns,
milestones as points, and phase (level-1) rollups when a parent has no explicit dates.

The router feeds plain dicts/dates in and renders the returned rows as HTML/CSS grid.
Keeping this layer pure is what lets the week→month boundary and rollups be tested
without a database.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from typing import Any, Optional

WEEKLY_LIMIT = 12  # first N weeks are weekly columns; the plan then coarsens to months


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _first_of_month(d: date) -> date:
    return d.replace(day=1)


def _last_of_month(d: date) -> date:
    return d.replace(day=monthrange(d.year, d.month)[1])


def _next_month(d: date) -> date:
    return _first_of_month(d) + timedelta(days=monthrange(d.year, d.month)[1])


def build_axis(start: date, end: date) -> dict[str, Any]:
    """Columns + top-row groups for the timeline header.

    Returns {"columns": [...], "groups": [...], "n": int}. Each column carries kind
    ('week'|'month'), start/end dates, a bottom `label` (S1../M4..), and a `group` key
    (calendar month for weeks, year for months) used to build colspan header groups.
    """
    if end < start:
        end = start
    columns: list[dict[str, Any]] = []

    wk = _monday(start)
    i = 0
    while i < WEEKLY_LIMIT:
        col_start = wk + timedelta(days=7 * i)
        col_end = col_start + timedelta(days=6)
        columns.append({
            "kind": "week", "start": col_start, "end": col_end,
            "label": f"S{i + 1}", "index": i + 1,
            "group": (col_start.year, col_start.month),
        })
        i += 1
        if col_end >= end:
            break

    last_week_end = columns[-1]["end"]
    if end > last_week_end:
        # Monthly region starts at the first-of-month AFTER the weekly region, so labels
        # land on M4.. (the template's "12 weeks ≈ 3 months, then Mes 4"). Using the month
        # *containing* last_week_end would double-count it and mislabel as M3.
        m = _next_month(last_week_end)
        while m <= end:
            gm = (m.year - start.year) * 12 + (m.month - start.month) + 1
            columns.append({
                "kind": "month", "start": m, "end": _last_of_month(m),
                "label": f"M{gm}", "index": gm, "month": m.month, "year": m.year,
                "group": (m.year, 0),
            })
            m = _next_month(m)

    groups: list[dict[str, Any]] = []
    for c in columns:
        if groups and groups[-1]["key"] == c["group"]:
            groups[-1]["span"] += 1
        else:
            groups.append({"key": c["group"], "span": 1,
                           "year": c["group"][0], "month": c["group"][1]})

    return {"columns": columns, "groups": groups, "n": len(columns)}


def today_fraction(today: date, columns: list[dict[str, Any]]) -> Optional[float]:
    """Horizontal position (0..1) of `today` across the columns, for the vertical marker.
    None when today falls outside the plan window."""
    if not columns:
        return None
    if today < columns[0]["start"] or today > columns[-1]["end"]:
        return None
    for idx, c in enumerate(columns):
        if c["start"] <= today <= c["end"]:
            # fraction of the way through this column, then across the whole axis
            span_days = (c["end"] - c["start"]).days + 1
            within = ((today - c["start"]).days + 0.5) / span_days
            return (idx + within) / len(columns)
    return None


def bar_geometry(
    task_start: Optional[date], task_end: Optional[date], columns: list[dict[str, Any]]
) -> Optional[tuple[int, int]]:
    """(start_col_index, span) for a bar, 0-based. None when undated or fully off-axis."""
    if task_start is None or task_end is None or not columns:
        return None
    if task_end < task_start:
        task_end = task_start
    if task_end < columns[0]["start"] or task_start > columns[-1]["end"]:
        return None
    start_idx: Optional[int] = None
    end_idx: Optional[int] = None
    for idx, c in enumerate(columns):
        if start_idx is None and c["end"] >= task_start:
            start_idx = idx
        if c["start"] <= task_end:
            end_idx = idx
    if start_idx is None:
        start_idx = 0
    if end_idx is None or end_idx < start_idx:
        end_idx = start_idx
    return (start_idx, end_idx - start_idx + 1)


def milestone_index(mdate: Optional[date], columns: list[dict[str, Any]]) -> Optional[int]:
    """Column index holding the milestone date, or None if undated / off-axis."""
    if mdate is None or not columns:
        return None
    if mdate < columns[0]["start"] or mdate > columns[-1]["end"]:
        return None
    for idx, c in enumerate(columns):
        if c["start"] <= mdate <= c["end"]:
            return idx
    return None


def _index(tasks: list[dict[str, Any]]) -> tuple[dict[Any, list], dict[Any, tuple]]:
    """Children-by-parent (sorted) + memoized effective (start, end) per task id."""
    by_parent: dict[Any, list[dict[str, Any]]] = {}
    for t in tasks:
        by_parent.setdefault(t.get("parent_id"), []).append(t)
    for lst in by_parent.values():
        lst.sort(key=lambda x: (x.get("sort_order", 0), x.get("name", ""), str(x.get("id"))))

    eff: dict[Any, tuple[Optional[date], Optional[date]]] = {}

    def compute(t: dict[str, Any]) -> tuple[Optional[date], Optional[date]]:
        s, e = t.get("start"), t.get("end")
        child_s: list[date] = []
        child_e: list[date] = []
        for c in by_parent.get(t["id"], []):
            cs, ce = compute(c)
            if cs is not None:
                child_s.append(cs)
            if ce is not None:
                child_e.append(ce)
        if s is None and child_s:
            s = min(child_s)
        if e is None and child_e:
            e = max(child_e)
        if t.get("is_milestone"):
            e = s  # a milestone is a point in time
        eff[t["id"]] = (s, e)
        return (s, e)

    for root in by_parent.get(None, []):
        compute(root)
    return by_parent, eff


def plan_bounds(tasks: list[dict[str, Any]]) -> tuple[Optional[date], Optional[date]]:
    """Min effective start / max effective end across the whole plan (for the axis)."""
    if not tasks:
        return (None, None)
    _, eff = _index(tasks)
    starts = [s for s, _ in eff.values() if s is not None]
    ends = [e for _, e in eff.values() if e is not None]
    return (min(starts) if starts else None, max(ends) if ends else None)


def plan_rows(tasks: list[dict[str, Any]], columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten the task forest to ordered render rows with level + bar/milestone geometry.

    Each task dict needs: id, parent_id, name, start, end, progress, is_milestone, deps,
    sort_order. Returns rows in depth-first order (phases then their children)."""
    by_parent, eff = _index(tasks)
    rows: list[dict[str, Any]] = []

    def emit(t: dict[str, Any], level: int, root: int) -> None:
        s, e = eff[t["id"]]
        is_ms = bool(t.get("is_milestone"))
        deps = t.get("deps") or []
        rows.append({
            "id": str(t["id"]),
            "name": t.get("name", ""),
            "level": level,
            "root": root,  # ordinal of the top-level phase — used to color bars by phase
            "start": s,
            "end": e,
            "progress": int(t.get("progress") or 0),
            "is_milestone": is_ms,
            "has_deps": bool(deps),
            "bar": None if is_ms else bar_geometry(s, e, columns),
            "milestone_idx": milestone_index(s, columns) if is_ms else None,
        })
        for c in by_parent.get(t["id"], []):
            emit(c, level + 1, root)

    for i, r in enumerate(by_parent.get(None, [])):
        emit(r, 1, i)
    return rows
