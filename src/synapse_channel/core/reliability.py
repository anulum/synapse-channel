# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — reliability memory from durable coordination events
"""Build evidence-only reliability memory from the durable hub event log.

The report is deliberately not a score, rank, or reputation system. It preserves
bounded operational signals that can help agents route work or write handovers:
stale live leases, declared failed-check evidence, broken handoff candidates, and
reconstructed path-conflict pairs. Each signal points back to event-log sequence
numbers and payload text so a human or policy layer can review the evidence.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore, StoredEvent

SNAPSHOT_KINDS = frozenset(
    {EventKind.CLAIM, EventKind.TASK_UPDATE, EventKind.CHECKPOINT, EventKind.HANDOFF}
)
"""Event kinds whose payload is a task-claim snapshot."""


@dataclass(frozen=True)
class ReliabilityFinding:
    """One evidence-backed reliability signal.

    Attributes
    ----------
    kind : str
        Signal kind: ``stale_claim``, ``declared_failed_check``,
        ``broken_handoff_candidate``, or ``conflict_pair``.
    owner : str
        Primary owner associated with the signal.
    task_id : str
        Primary task id associated with the signal.
    seq : int
        Durable event-log sequence where the signal was observed.
    ts : float
        Event timestamp.
    detail : str
        Short human-readable description.
    evidence : dict[str, Any]
        Stable machine-readable evidence fields.
    """

    kind: str
    owner: str
    task_id: str
    seq: int
    ts: float
    detail: str
    evidence: dict[str, Any]


@dataclass(frozen=True)
class OwnerReliabilitySummary:
    """Evidence counts for one owner.

    The fields are counts, not a grade. Consumers may inspect the corresponding
    findings before making routing or process decisions.
    """

    owner: str
    stale_claims: int = 0
    declared_failed_checks: int = 0
    broken_handoffs: int = 0
    conflict_pairs: int = 0


@dataclass(frozen=True)
class ReliabilityReport:
    """Evidence-only reliability memory built from a durable event store."""

    generated_from_seq: int
    as_of: float
    findings: tuple[ReliabilityFinding, ...]
    owners: tuple[OwnerReliabilitySummary, ...]

    @property
    def summary_by_owner(self) -> dict[str, OwnerReliabilitySummary]:
        """Return owner summaries keyed by owner name."""
        return {summary.owner: summary for summary in self.owners}


def run_reliability_report(
    db_path: str | Path,
    *,
    as_of: float | None = None,
    key_file: str | Path | None = None,
) -> ReliabilityReport:
    """Build reliability memory from an existing SQLite event store.

    Parameters
    ----------
    db_path : str or pathlib.Path
        Path to a hub event-store database.
    as_of : float or None, optional
        Timestamp used to decide whether live claims and handoffs are stale. The
        latest event timestamp is used when omitted.
    key_file : str or pathlib.Path or None, optional
        Owner-only SQLCipher key when ``db_path`` is an encrypted store.

    Returns
    -------
    ReliabilityReport
        Evidence-only reliability report.

    Raises
    ------
    ValueError
        If the event store does not exist.
    """
    path = Path(db_path)
    if not path.exists():
        msg = f"missing event store: {path}"
        raise ValueError(msg)
    store = EventStore(path, key_file=key_file)
    try:
        events = tuple(store.read_all())
    finally:
        store.close()
    return build_reliability_report(events, as_of=as_of)


def build_reliability_report(
    events: Sequence[StoredEvent],
    *,
    as_of: float | None = None,
) -> ReliabilityReport:
    """Build evidence-only reliability memory from loaded events."""
    cutoff = _effective_as_of(events, as_of)
    findings = tuple(
        sorted(
            (
                *_declared_failed_checks(events),
                *_conflict_pairs(events),
                *_stale_claims(events, cutoff),
                *_broken_handoff_candidates(events, cutoff),
            ),
            key=lambda finding: (finding.seq, finding.kind, finding.owner, finding.task_id),
        )
    )
    return ReliabilityReport(
        generated_from_seq=max((event.seq for event in events), default=0),
        as_of=cutoff,
        findings=findings,
        owners=_summaries(findings),
    )


def reliability_to_json(report: ReliabilityReport) -> dict[str, object]:
    """Return a stable JSON-compatible representation of a reliability report."""
    return {
        "generated_from_seq": report.generated_from_seq,
        "as_of": report.as_of,
        "owners": [_summary_to_json(summary) for summary in report.owners],
        "findings": [_finding_to_json(finding) for finding in report.findings],
        "note": "audit signals, not scores",
    }


def render_human(report: ReliabilityReport) -> str:
    """Render reliability memory as compact terminal text."""
    header = "Reliability memory: audit signals, not scores"
    if not report.findings:
        return f"{header}\n\nNo reliability signals found."
    lines = [
        header,
        f"generated_from_seq={report.generated_from_seq} as_of={report.as_of:.3f}",
        "",
        "Owners",
    ]
    lines.extend(_render_summary(summary) for summary in report.owners)
    lines.append("")
    lines.append("Findings")
    lines.extend(_render_finding(finding) for finding in report.findings)
    return "\n".join(lines)


def _effective_as_of(events: Sequence[StoredEvent], explicit: float | None) -> float:
    """Return the cutoff timestamp for stale-signal reconstruction."""
    if explicit is not None:
        return float(explicit)
    return max((event.ts for event in events), default=0.0)


def _declared_failed_checks(events: Sequence[StoredEvent]) -> tuple[ReliabilityFinding, ...]:
    """Return declared failed-check evidence from progress notes."""
    findings: list[ReliabilityFinding] = []
    for event in events:
        if event.kind != EventKind.LEDGER_PROGRESS:
            continue
        text = str(event.payload.get("text", ""))
        lowered = text.lower()
        if (
            "known_failures=" not in lowered
            and "failed" not in lowered
            and "failure" not in lowered
        ):
            continue
        owner = str(event.payload.get("author", ""))
        task_id = str(event.payload.get("task_id", ""))
        findings.append(
            ReliabilityFinding(
                kind="declared_failed_check",
                owner=owner,
                task_id=task_id,
                seq=event.seq,
                ts=event.ts,
                detail=text,
                evidence={"progress_kind": str(event.payload.get("kind", "")), "text": text},
            )
        )
    return tuple(findings)


def _stale_claims(
    events: Sequence[StoredEvent],
    as_of: float,
) -> tuple[ReliabilityFinding, ...]:
    """Return live claims whose lease expired by ``as_of``."""
    live = _live_snapshots(events)
    findings: list[ReliabilityFinding] = []
    for task_id, event in sorted(live.items()):
        expires_at = _lease_expires_at(event)
        if expires_at > as_of:
            continue
        owner = _owner(event)
        findings.append(
            ReliabilityFinding(
                kind="stale_claim",
                owner=owner,
                task_id=task_id,
                seq=event.seq,
                ts=event.ts,
                detail=f"lease expired at {expires_at:.3f} by as_of {as_of:.3f}",
                evidence={
                    "lease_expires_at": expires_at,
                    "as_of": as_of,
                    "paths": list(_paths(event)),
                    "worktree": _worktree(event),
                },
            )
        )
    return tuple(findings)


def _broken_handoff_candidates(
    events: Sequence[StoredEvent],
    as_of: float,
) -> tuple[ReliabilityFinding, ...]:
    """Return handoffs whose recipient lease expired before a recipient update."""
    findings: list[ReliabilityFinding] = []
    for index, event in enumerate(events):
        if event.kind != EventKind.HANDOFF:
            continue
        task_id = _task_id(event)
        owner = _owner(event)
        if _recipient_followed_up(events[index + 1 :], task_id=task_id, owner=owner):
            continue
        expires_at = _lease_expires_at(event)
        if expires_at > as_of:
            continue
        findings.append(
            ReliabilityFinding(
                kind="broken_handoff_candidate",
                owner=owner,
                task_id=task_id,
                seq=event.seq,
                ts=event.ts,
                detail=(
                    f"handoff recipient had no later task update/checkpoint/release "
                    f"before lease expiry {expires_at:.3f}"
                ),
                evidence={"lease_expires_at": expires_at, "as_of": as_of},
            )
        )
    return tuple(findings)


def _recipient_followed_up(
    events: Sequence[StoredEvent],
    *,
    task_id: str,
    owner: str,
) -> bool:
    """Return whether a handoff recipient later produced task activity."""
    for event in events:
        if _task_id(event) != task_id:
            continue
        if event.kind == EventKind.RELEASE:
            return True
        if event.kind in {EventKind.TASK_UPDATE, EventKind.CHECKPOINT, EventKind.HANDOFF}:
            return _owner(event) == owner
    return False


def _conflict_pairs(events: Sequence[StoredEvent]) -> tuple[ReliabilityFinding, ...]:
    """Return unique reconstructed path-overlap conflict pairs."""
    live: dict[str, StoredEvent] = {}
    seen: set[tuple[str, str, str, str, tuple[str, ...]]] = set()
    findings: list[ReliabilityFinding] = []
    for event in events:
        task_id = _task_id(event)
        if event.kind == EventKind.RELEASE and task_id:
            live.pop(task_id, None)
            continue
        if event.kind not in SNAPSHOT_KINDS or not task_id:
            continue
        live[task_id] = event
        for left_task, left in sorted(live.items()):
            for right_task, right in sorted(live.items()):
                if left_task >= right_task:
                    continue
                if _owner(left) == _owner(right) or _worktree(left) != _worktree(right):
                    continue
                if not _paths_overlap_many(_paths(left), _paths(right)):
                    continue
                paths = _unique_ordered((*_paths(left), *_paths(right)))
                key = (left_task, _owner(left), right_task, _owner(right), paths)
                if key in seen:
                    continue
                seen.add(key)
                findings.extend(
                    _conflict_findings(event, left_task, left, right_task, right, paths)
                )
    return tuple(findings)


def _conflict_findings(
    event: StoredEvent,
    left_task: str,
    left: StoredEvent,
    right_task: str,
    right: StoredEvent,
    paths: tuple[str, ...],
) -> tuple[ReliabilityFinding, ReliabilityFinding]:
    """Return one conflict finding for each owner in a pair."""
    evidence = {
        "left_task": left_task,
        "left_owner": _owner(left),
        "right_task": right_task,
        "right_owner": _owner(right),
        "worktree": _worktree(left),
        "paths": list(paths),
    }
    return (
        ReliabilityFinding(
            kind="conflict_pair",
            owner=_owner(left),
            task_id=left_task,
            seq=event.seq,
            ts=event.ts,
            detail=f"{left_task}@{_owner(left)} overlaps {right_task}@{_owner(right)}",
            evidence=evidence,
        ),
        ReliabilityFinding(
            kind="conflict_pair",
            owner=_owner(right),
            task_id=right_task,
            seq=event.seq,
            ts=event.ts,
            detail=f"{right_task}@{_owner(right)} overlaps {left_task}@{_owner(left)}",
            evidence=evidence,
        ),
    )


def _live_snapshots(events: Sequence[StoredEvent]) -> dict[str, StoredEvent]:
    """Return latest unreleased task snapshots."""
    live: dict[str, StoredEvent] = {}
    for event in events:
        task_id = _task_id(event)
        if event.kind == EventKind.RELEASE and task_id:
            live.pop(task_id, None)
        elif event.kind in SNAPSHOT_KINDS and task_id:
            live[task_id] = event
    return live


def _summaries(findings: Sequence[ReliabilityFinding]) -> tuple[OwnerReliabilitySummary, ...]:
    """Aggregate findings into owner-count summaries."""
    metric_by_kind = {
        "stale_claim": "stale_claims",
        "declared_failed_check": "declared_failed_checks",
        "broken_handoff_candidate": "broken_handoffs",
        "conflict_pair": "conflict_pairs",
    }
    counts: dict[str, dict[str, int]] = {}
    for finding in findings:
        bucket = counts.setdefault(
            finding.owner,
            {
                "stale_claims": 0,
                "declared_failed_checks": 0,
                "broken_handoffs": 0,
                "conflict_pairs": 0,
            },
        )
        bucket[metric_by_kind[finding.kind]] += 1
    return tuple(
        OwnerReliabilitySummary(
            owner=owner,
            stale_claims=values["stale_claims"],
            declared_failed_checks=values["declared_failed_checks"],
            broken_handoffs=values["broken_handoffs"],
            conflict_pairs=values["conflict_pairs"],
        )
        for owner, values in sorted(counts.items())
    )


def _task_id(event: StoredEvent) -> str:
    """Return a task id carried by an event payload."""
    return str(event.payload.get("task_id", ""))


def _owner(event: StoredEvent) -> str:
    """Return the owner carried by a task snapshot."""
    return str(event.payload.get("owner", ""))


def _worktree(event: StoredEvent) -> str:
    """Return the worktree carried by a task snapshot."""
    return str(event.payload.get("worktree", ""))


def _lease_expires_at(event: StoredEvent) -> float:
    """Return the lease expiry timestamp carried by a task snapshot."""
    return float(event.payload.get("lease_expires_at", 0.0))


def _paths(event: StoredEvent) -> tuple[str, ...]:
    """Return path scopes carried by a task snapshot."""
    return tuple(str(path) for path in event.payload.get("paths", ()))


def _paths_overlap_many(left: Sequence[str], right: Sequence[str]) -> bool:
    """Return whether two path-scope sets overlap."""
    if not left or not right:
        return True
    return any(
        _path_pair_overlaps(left_path, right_path) for left_path in left for right_path in right
    )


def _path_pair_overlaps(left: str, right: str) -> bool:
    """Return whether two repository-relative paths overlap."""
    left_clean = left.rstrip("/")
    right_clean = right.rstrip("/")
    return (
        left_clean == right_clean
        or left_clean.startswith(f"{right_clean}/")
        or right_clean.startswith(f"{left_clean}/")
    )


def _unique_ordered(values: Iterable[str]) -> tuple[str, ...]:
    """Return non-empty values without duplicates while preserving order."""
    return tuple(dict.fromkeys(value for value in values if value))


def _summary_to_json(summary: OwnerReliabilitySummary) -> dict[str, object]:
    """Convert an owner summary into JSON-compatible fields."""
    return {
        "owner": summary.owner,
        "stale_claims": summary.stale_claims,
        "declared_failed_checks": summary.declared_failed_checks,
        "broken_handoffs": summary.broken_handoffs,
        "conflict_pairs": summary.conflict_pairs,
    }


def _finding_to_json(finding: ReliabilityFinding) -> dict[str, object]:
    """Convert a finding into JSON-compatible fields."""
    return {
        "kind": finding.kind,
        "owner": finding.owner,
        "task_id": finding.task_id,
        "seq": finding.seq,
        "ts": finding.ts,
        "detail": finding.detail,
        "evidence": finding.evidence,
    }


def _render_summary(summary: OwnerReliabilitySummary) -> str:
    """Render one owner summary."""
    return (
        f"- {summary.owner}: stale_claims={summary.stale_claims} "
        f"declared_failed_checks={summary.declared_failed_checks} "
        f"broken_handoffs={summary.broken_handoffs} conflict_pairs={summary.conflict_pairs}"
    )


def _render_finding(finding: ReliabilityFinding) -> str:
    """Render one finding."""
    return (
        f"- seq={finding.seq} kind={finding.kind} owner={finding.owner} "
        f"task={finding.task_id} — {finding.detail}"
    )
