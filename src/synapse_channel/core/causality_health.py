# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — flag anomalies in the coordination-causality graph
"""Flag coordination anomalies the causality graph makes visible.

:mod:`synapse_channel.core.causality` reconstructs what *happened*;
:mod:`synapse_channel.core.reliability` reports per-agent operational evidence
without graph analysis. This module sits between them: it walks each task's
recorded lifecycle in the causal graph and flags three shapes that usually mean
coordination went wrong — a **claim with no lifecycle successor** (an agent
claimed work and then went silent), a **declared dependency that never
completed** (the dependent's recorded prerequisite is unmet, using exactly the
completion predicate the dependency-edge derivation uses, so the two never
disagree), and a **claim never released** whose task has been silent longer
than a threshold (a lease that outlived its owner).

Honest scope: every signal is derived from *recorded* events only, and ages
are measured against the log's own final timestamp — never the wall clock — so
the assessment is deterministic over a given log and a report can be replayed
byte-for-byte. An anomaly is an operator signal, not a verdict: an orphaned
claim may be an agent mid-work that simply has not reported, and a dangling
dependency may complete tomorrow. Like every causality mode this is read-only
over the durable log and contacts no live hub.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.causality import (
    DEFAULT_MAX_GRAPH_NODES,
    DONE_STATUSES,
    GRAPH_KINDS,
    CausalNode,
    build_causal_graph,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.lifecycle import TaskStatus
from synapse_channel.core.persistence import EventStore, StoredEvent

DEFAULT_STALE_AFTER = 3600.0
"""Seconds of log-relative silence after which an unreleased claim is stale."""


@dataclass(frozen=True)
class OrphanedClaim:
    """A claim that is its task's last recorded event — claimed, then silence.

    Attributes
    ----------
    task_id : str
        The claimed task.
    owner : str
        The claiming agent, or empty when the claim recorded none.
    seq : int
        Event sequence of the claim.
    ts : float
        Timestamp of the claim.
    age_seconds : float
        Silence since the claim, measured to the log's final timestamp.
    """

    task_id: str
    owner: str
    seq: int
    ts: float
    age_seconds: float


@dataclass(frozen=True)
class DanglingDependency:
    """A declared dependency whose task never completed anywhere in the log.

    Completion means exactly what the dependency-edge derivation means by it —
    a release of the dependency task, or a snapshot carrying a done-family
    status — so a dependency this class flags is precisely one no
    ``dependency`` edge could ever have satisfied.

    Attributes
    ----------
    task_id : str
        The dependent task that declared the requirement.
    depends_on : str
        The dependency task that never completed.
    declared_seq : int
        Event sequence of the ledger declaration.
    """

    task_id: str
    depends_on: str
    declared_seq: int


@dataclass(frozen=True)
class StaleClaim:
    """A claimed task never released, silent longer than the threshold.

    Attributes
    ----------
    task_id : str
        The claimed task.
    owner : str
        The last recorded owner, or empty.
    last_seq : int
        Sequence of the task's last recorded event.
    last_ts : float
        Timestamp of the task's last recorded event.
    age_seconds : float
        Silence since that event, measured to the log's final timestamp.
    """

    task_id: str
    owner: str
    last_seq: int
    last_ts: float
    age_seconds: float


@dataclass(frozen=True)
class CausalHealthReport:
    """The anomaly assessment of one event log.

    Attributes
    ----------
    orphaned : tuple[OrphanedClaim, ...]
        Claims that are their task's final recorded event.
    dangling : tuple[DanglingDependency, ...]
        Declared dependencies that never completed.
    stale : tuple[StaleClaim, ...]
        Unreleased claims silent longer than ``stale_after``.
    tasks_scanned : int
        Tasks whose lifecycles were walked.
    log_end_ts : float
        The log's final coordination-event timestamp — the clock every age is
        measured against; ``0.0`` for an empty log.
    stale_after : float
        The staleness threshold the assessment used, in seconds.
    """

    orphaned: tuple[OrphanedClaim, ...]
    dangling: tuple[DanglingDependency, ...]
    stale: tuple[StaleClaim, ...]
    tasks_scanned: int
    log_end_ts: float
    stale_after: float

    @property
    def anomaly_count(self) -> int:
        """Total flagged anomalies across the three signals."""
        return len(self.orphaned) + len(self.dangling) + len(self.stale)


def assess_causal_health(
    events: Sequence[StoredEvent],
    *,
    stale_after: float = DEFAULT_STALE_AFTER,
) -> CausalHealthReport:
    """Walk each task's recorded lifecycle and flag the three anomaly shapes.

    Parameters
    ----------
    events : Sequence[StoredEvent]
        Loaded events, in any order.
    stale_after : float, optional
        Seconds of log-relative silence after which an unreleased claim is
        flagged stale. Defaults to :data:`DEFAULT_STALE_AFTER`.

    Returns
    -------
    CausalHealthReport
        The deterministic assessment; anomalies in task first-seen order.
    """
    graph = build_causal_graph(events)
    by_task: dict[str, list[CausalNode]] = defaultdict(list)
    for node in graph.nodes:
        if node.task_id:
            by_task[node.task_id].append(node)
    log_end_ts = max((node.ts for node in graph.nodes), default=0.0)
    completed = {
        node.task_id
        for node in graph.nodes
        if node.task_id and (node.kind == EventKind.RELEASE or node.status in DONE_STATUSES)
    }
    orphaned: list[OrphanedClaim] = []
    dangling: list[DanglingDependency] = []
    stale: list[StaleClaim] = []
    for task_id, nodes in by_task.items():
        last = nodes[-1]
        if last.kind == EventKind.CLAIM:
            orphaned.append(
                OrphanedClaim(
                    task_id=task_id,
                    owner=last.owner,
                    seq=last.seq,
                    ts=last.ts,
                    age_seconds=log_end_ts - last.ts,
                )
            )
        for node in nodes:
            if node.kind != EventKind.LEDGER_TASK:
                continue
            for dependency in node.depends_on:
                if dependency not in completed:
                    dangling.append(
                        DanglingDependency(
                            task_id=task_id,
                            depends_on=dependency,
                            declared_seq=node.seq,
                        )
                    )
        if _is_stale(task_id, nodes, completed, log_end_ts, stale_after):
            owner = next((node.owner for node in reversed(nodes) if node.owner), "")
            stale.append(
                StaleClaim(
                    task_id=task_id,
                    owner=owner,
                    last_seq=last.seq,
                    last_ts=last.ts,
                    age_seconds=log_end_ts - last.ts,
                )
            )
    return CausalHealthReport(
        orphaned=tuple(orphaned),
        dangling=tuple(dangling),
        stale=tuple(stale),
        tasks_scanned=len(by_task),
        log_end_ts=log_end_ts,
        stale_after=stale_after,
    )


def run_causal_health(
    db_path: str | Path,
    *,
    max_nodes: int | None = DEFAULT_MAX_GRAPH_NODES,
    stale_after: float = DEFAULT_STALE_AFTER,
) -> CausalHealthReport:
    """Assess an existing SQLite event store, streaming and bounded.

    Parameters
    ----------
    db_path : str or pathlib.Path
        Path to a hub event-store database.
    max_nodes : int or None, optional
        Fail-closed ceiling on coordination events; ``None`` or ``0`` lifts it.
    stale_after : float, optional
        Staleness threshold in seconds.

    Returns
    -------
    CausalHealthReport
        The assessment built from persisted events.

    Raises
    ------
    ValueError
        If the event store does not exist or the log holds more coordination
        events than ``max_nodes``.
    """
    path = Path(db_path)
    if not path.exists():
        msg = f"missing event store: {path}"
        raise ValueError(msg)
    store = EventStore(path)
    try:
        events: list[StoredEvent] = []
        for event in store.iter_events(kinds=GRAPH_KINDS):
            events.append(event)
            if max_nodes and len(events) > max_nodes:
                msg = (
                    f"causal health scan would exceed {max_nodes} coordination events; "
                    f"bound the log with `synapse compact` or raise --max-nodes"
                )
                raise ValueError(msg)
    finally:
        store.close()
    return assess_causal_health(events, stale_after=stale_after)


def health_to_json(report: CausalHealthReport) -> dict[str, object]:
    """Return a stable JSON-compatible representation of a health report."""
    return {
        "tasks_scanned": report.tasks_scanned,
        "log_end_ts": report.log_end_ts,
        "stale_after": report.stale_after,
        "anomaly_count": report.anomaly_count,
        "orphaned": [
            {
                "task_id": item.task_id,
                "owner": item.owner,
                "seq": item.seq,
                "ts": item.ts,
                "age_seconds": item.age_seconds,
            }
            for item in report.orphaned
        ],
        "dangling": [
            {
                "task_id": item.task_id,
                "depends_on": item.depends_on,
                "declared_seq": item.declared_seq,
            }
            for item in report.dangling
        ],
        "stale": [
            {
                "task_id": item.task_id,
                "owner": item.owner,
                "last_seq": item.last_seq,
                "last_ts": item.last_ts,
                "age_seconds": item.age_seconds,
            }
            for item in report.stale
        ],
        "note": "recorded-event signals, not verdicts",
    }


def render_health_markdown(report: CausalHealthReport) -> str:
    """Render a health report as compact Markdown."""
    lines = [
        f"# Causal health: {report.anomaly_count} anomal"
        f"{'y' if report.anomaly_count == 1 else 'ies'} "
        f"across {report.tasks_scanned} task(s)",
        "",
        f"- Ages measured to the log's final event (ts={report.log_end_ts:.3f})",
        f"- Stale threshold: {report.stale_after:.0f}s",
        "",
        "## Orphaned claims (claimed, then silence)",
    ]
    if report.orphaned:
        lines.extend(
            f"- seq={item.seq} task={item.task_id}"
            f"{f' owner={item.owner}' if item.owner else ''}"
            f" silent {item.age_seconds:.0f}s"
            for item in report.orphaned
        )
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Dangling dependencies (declared, never completed)")
    if report.dangling:
        lines.extend(
            f"- seq={item.declared_seq} task={item.task_id} depends on "
            f"{item.depends_on}, which never completed"
            for item in report.dangling
        )
    else:
        lines.append("- none")
    lines.append("")
    lines.append(f"## Stale claims (unreleased, silent > {report.stale_after:.0f}s)")
    if report.stale:
        lines.extend(
            f"- task={item.task_id}"
            f"{f' owner={item.owner}' if item.owner else ''}"
            f" last event seq={item.last_seq}, silent {item.age_seconds:.0f}s"
            for item in report.stale
        )
    else:
        lines.append("- none")
    return "\n".join(lines)


def _is_stale(
    task_id: str,
    nodes: Sequence[CausalNode],
    completed: set[str],
    log_end_ts: float,
    stale_after: float,
) -> bool:
    """Return whether a task holds an unreleased claim silent past the threshold.

    A task is a staleness candidate only when it was actually claimed, never
    completed (no release, no done-family status — the same completion set
    ``dangling`` checks), and did not end failed: a failed terminal is a
    reported outcome, not a silent lease.
    """
    if task_id in completed:
        return False
    if not any(node.kind == EventKind.CLAIM for node in nodes):
        return False
    last = nodes[-1]
    if last.status == TaskStatus.FAILED:
        return False
    return (log_end_ts - last.ts) > stale_after
