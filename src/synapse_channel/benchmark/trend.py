# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable benchmark history with per-metric trend rendering
"""Accumulate benchmark scorecards in SQLite and render their trends.

One scorecard is a snapshot; a performance story needs the runs *over time*.
This module appends each finished scorecard to a local SQLite store and
renders, per probe metric, the series across every stored run — a sparkline,
the first and latest values, and the observed range — so a slow regression
that no single ``--compare`` gate catches is still visible. The sparkline
ramp is Unicode blocks by default with a printable-ASCII alternative for
consoles and CI log viewers without UTF-8.

Honest scope, same doctrine as the scorecard itself: numbers from different
host contexts do not form one comparable series, so a change of CPU model,
frequency governor, or package version between consecutive runs is rendered
as an explicit **context break** annotation rather than silently connected —
and unlike ``--compare``, a differing CPU model is annotated, not refused,
because history legitimately spans upgrades. The store is a plain SQLite
file the operator owns; nothing uploads anywhere.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.benchmark.scorecard import Scorecard, scorecard_to_json

SPARK_LEVELS = "▁▂▃▄▅▆▇█"
"""Sparkline glyphs, lowest to highest."""

ASCII_SPARK_LEVELS = "._-=+*#%@"
"""Printable-ASCII sparkline glyphs, lowest to highest.

For consoles and CI log viewers without UTF-8: every glyph is 7-bit
ASCII, ordered by visual weight, so the same series reads the same way
the block glyphs do.
"""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS benchmark_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL NOT NULL,
    package_version TEXT NOT NULL,
    cpu_model TEXT NOT NULL,
    governor TEXT NOT NULL,
    scorecard_json TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class StoredRun:
    """One scorecard as read back from the trend store.

    Attributes
    ----------
    run_id : int
        The store's row id, monotonically increasing per append.
    started_at : float
        UNIX timestamp the run started.
    package_version : str
        Installed package version of the run.
    cpu_model : str
        CPU model the run executed on.
    governor : str
        CPU-frequency governor during the run.
    metrics : dict[str, dict[str, float]]
        Probe name → metric name → value, exactly as the scorecard
        recorded them.
    """

    run_id: int
    started_at: float
    package_version: str
    cpu_model: str
    governor: str
    metrics: dict[str, dict[str, float]]


@dataclass(frozen=True)
class ContextBreak:
    """A host/package context change between two consecutive stored runs.

    Attributes
    ----------
    before_run_id : int
        The run id the break precedes.
    changes : tuple[str, ...]
        Human-readable ``field old→new`` descriptions.
    """

    before_run_id: int
    changes: tuple[str, ...]


def append_scorecard(db_path: str | Path, scorecard: Scorecard) -> int:
    """Append one finished scorecard to the trend store.

    Parameters
    ----------
    db_path : str or pathlib.Path
        The SQLite trend store; created (with parents) when absent.
    scorecard : Scorecard
        The finished run to record, stored as its full JSON document.

    Returns
    -------
    int
        The stored run's id.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    context = scorecard.context
    connection = sqlite3.connect(path)
    try:
        connection.execute(_SCHEMA)
        cursor = connection.execute(
            "INSERT INTO benchmark_runs "
            "(started_at, package_version, cpu_model, governor, scorecard_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                context.started_at,
                context.package_version,
                context.cpu_model,
                context.governor,
                json.dumps(scorecard_to_json(scorecard), sort_keys=True),
            ),
        )
        connection.commit()
        return int(cursor.lastrowid or 0)
    finally:
        connection.close()


def load_history(db_path: str | Path) -> tuple[StoredRun, ...]:
    """Load every stored run, oldest first.

    Parameters
    ----------
    db_path : str or pathlib.Path
        The SQLite trend store.

    Returns
    -------
    tuple[StoredRun, ...]
        Stored runs ordered by start time, then row id.

    Raises
    ------
    ValueError
        If the store does not exist.
    """
    path = Path(db_path)
    if not path.exists():
        msg = f"missing trend store: {path}"
        raise ValueError(msg)
    connection = sqlite3.connect(path)
    try:
        connection.execute(_SCHEMA)
        rows = connection.execute(
            "SELECT id, started_at, package_version, cpu_model, governor, scorecard_json "
            "FROM benchmark_runs ORDER BY started_at, id"
        ).fetchall()
    finally:
        connection.close()
    return tuple(_stored_run(row) for row in rows)


def context_breaks(runs: tuple[StoredRun, ...]) -> tuple[ContextBreak, ...]:
    """Return every host/package context change between consecutive runs."""
    breaks: list[ContextBreak] = []
    for previous, current in zip(runs, runs[1:], strict=False):
        changes = [
            f"{field} {before}→{after}"
            for field, before, after in (
                ("package", previous.package_version, current.package_version),
                ("cpu", previous.cpu_model, current.cpu_model),
                ("governor", previous.governor, current.governor),
            )
            if before != after
        ]
        if changes:
            breaks.append(ContextBreak(before_run_id=current.run_id, changes=tuple(changes)))
    return tuple(breaks)


def sparkline(values: list[float], levels: str = SPARK_LEVELS) -> str:
    """Render values as one sparkline glyph each (flat series renders mid-level).

    Parameters
    ----------
    values : list of float
        The series to render, oldest first.
    levels : str
        The glyph ramp, lowest to highest; :data:`SPARK_LEVELS` by
        default, :data:`ASCII_SPARK_LEVELS` for pure-ASCII output.

    Raises
    ------
    ValueError
        If ``levels`` is empty.
    """
    if not levels:
        msg = "sparkline needs at least one glyph level"
        raise ValueError(msg)
    if not values:
        return ""
    lowest, highest = min(values), max(values)
    if highest == lowest:
        return levels[(len(levels) - 1) // 2] * len(values)
    span = highest - lowest
    top = len(levels) - 1
    return "".join(levels[round((value - lowest) / span * top)] for value in values)


def trend_to_json(runs: tuple[StoredRun, ...]) -> dict[str, object]:
    """Return a stable JSON-compatible representation of the stored history."""
    return {
        "runs": [
            {
                "run_id": run.run_id,
                "started_at": run.started_at,
                "package_version": run.package_version,
                "cpu_model": run.cpu_model,
                "governor": run.governor,
                "metrics": run.metrics,
            }
            for run in runs
        ],
        "context_breaks": [
            {"before_run_id": item.before_run_id, "changes": list(item.changes)}
            for item in context_breaks(runs)
        ],
        "note": "host-dependent series; compare within one context segment",
    }


def render_trend_human(runs: tuple[StoredRun, ...], *, ascii_glyphs: bool = False) -> str:
    """Render the stored history as per-metric sparkline trend lines.

    Parameters
    ----------
    runs : tuple of StoredRun
        The stored history, oldest first.
    ascii_glyphs : bool
        When true, the whole trend block is printable ASCII: the
        :data:`ASCII_SPARK_LEVELS` ramp replaces the Unicode blocks and
        the arrow and dash punctuation degrade to ``->`` and ``--``, so
        the output survives consoles and CI log viewers without UTF-8.
    """
    levels = ASCII_SPARK_LEVELS if ascii_glyphs else SPARK_LEVELS
    arrow = "->" if ascii_glyphs else "→"
    if not runs:
        return "Benchmark trend: no stored runs."
    lines = [f"Benchmark trend: {len(runs)} stored run(s)"]
    breaks = context_breaks(runs)
    for item in breaks:
        changes = ", ".join(item.changes)
        if ascii_glyphs:
            changes = changes.replace("→", "->")
        lines.append(f"  context break before run {item.before_run_id}: {changes}")
    for probe, metric in _series_keys(runs):
        series = [
            (run.run_id, run.metrics[probe][metric])
            for run in runs
            if metric in run.metrics.get(probe, {})
        ]
        values = [value for _, value in series]
        if len(values) < 2:
            dash = "--" if ascii_glyphs else "—"
            lines.append(f"{probe} {metric}: {values[0]:,.2f} (1 run {dash} no trend yet)")
            continue
        lines.append(
            f"{probe} {metric}: {sparkline(values, levels)} "
            f"{values[0]:,.2f} {arrow} {values[-1]:,.2f} "
            f"(min {min(values):,.2f}, max {max(values):,.2f}, {len(values)} runs)"
        )
    return "\n".join(lines)


def _series_keys(runs: tuple[StoredRun, ...]) -> list[tuple[str, str]]:
    """Return every (probe, metric) pair seen across the runs, sorted."""
    keys = {
        (probe, metric)
        for run in runs
        for probe, metrics in run.metrics.items()
        for metric in metrics
    }
    return sorted(keys)


def _stored_run(row: tuple[int, float, str, str, str, str]) -> StoredRun:
    """Rebuild one stored run from its database row."""
    run_id, started_at, package_version, cpu_model, governor, scorecard_json = row
    document = json.loads(scorecard_json)
    metrics: dict[str, dict[str, float]] = {}
    for result in document.get("results", []):
        metrics[str(result["name"])] = {
            str(name): float(value) for name, value in result.get("metrics", {}).items()
        }
    return StoredRun(
        run_id=run_id,
        started_at=started_at,
        package_version=package_version,
        cpu_model=cpu_model,
        governor=governor,
        metrics=metrics,
    )
