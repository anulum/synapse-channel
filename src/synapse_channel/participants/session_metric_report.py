# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read opt-in session telemetry back from the durable event log
"""Aggregate opt-in operational session telemetry from the durable hub event log.

This is the read side of :mod:`synapse_channel.participants.session_metric_emit`, and the
operational counterpart to :mod:`synapse_channel.core.accounting`: where the accounting report
answers *what models cost*, this answers *how sessions are going* across processes. It reads the
``session_metric`` notes back from a hub event store and reduces them to one snapshot per session.

Because a session snapshot is **cumulative** — every emission for a session supersedes the prior
one — the reducer keeps the **latest** snapshot per ``(agent, session)`` pair (highest sequence
wins) rather than summing snapshots, then totals across sessions. The result is descriptive
evidence an advisor or operator can act on; like the accounting report it is evidence, never an
enforcement gate. The hub core is untouched: this only reads the existing progress-ledger channel.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.participants.session_metric_note import (
    SESSION_METRIC_NOTE_KIND,
    parse_session_metric_note,
)


@dataclass(frozen=True)
class SessionMetricRecord:
    """The latest operational snapshot observed for one session.

    Attributes
    ----------
    agent : str
        Recording agent identity (the progress-note author).
    session_id : str
        Session the snapshot was recorded against; carried as the note's task id.
    turns : int
        Turns folded into the session so far.
    errors : int
        Turns that ended in an error.
    abstentions : int
        Turns that abstained (no error, no answer).
    input_tokens : int
        Cumulative prompt/input tokens across turns.
    output_tokens : int
        Cumulative completion/output tokens across turns.
    cost_usd : float
        Cumulative metered spend across turns.
    total_latency_seconds : float
        Cumulative wall-clock time spent in turns.
    max_rate_limit_utilisation : float or None
        Highest rate-limit utilisation seen on any turn, or ``None`` when never reported.
    last_input_tokens : int
        The most recent turn's input tokens — the current context-pressure signal.
    seq : int
        Durable event-log sequence the snapshot was observed at.
    ts : float
        Event timestamp.
    """

    agent: str
    session_id: str
    turns: int
    errors: int
    abstentions: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    total_latency_seconds: float
    max_rate_limit_utilisation: float | None
    last_input_tokens: int
    seq: int
    ts: float

    @property
    def total_tokens(self) -> int:
        """Return the sum of cumulative input and output tokens."""
        return self.input_tokens + self.output_tokens

    @property
    def error_rate(self) -> float:
        """Return the fraction of turns that errored, or ``0.0`` before any turn."""
        return self.errors / self.turns if self.turns else 0.0

    @property
    def mean_latency_seconds(self) -> float:
        """Return the mean turn latency, or ``0.0`` before any turn."""
        return self.total_latency_seconds / self.turns if self.turns else 0.0


@dataclass(frozen=True)
class SessionMetricTotals:
    """Fleet-wide operational totals across every session in the report.

    Attributes
    ----------
    sessions : int
        Number of distinct ``(agent, session)`` snapshots summed.
    turns : int
        Total turns across sessions.
    errors : int
        Total errored turns across sessions.
    abstentions : int
        Total abstained turns across sessions.
    input_tokens : int
        Total cumulative input tokens across sessions.
    output_tokens : int
        Total cumulative output tokens across sessions.
    cost_usd : float
        Total cumulative spend across sessions.
    total_latency_seconds : float
        Total cumulative turn latency across sessions.
    max_rate_limit_utilisation : float or None
        Highest rate-limit utilisation seen across any session, or ``None`` when never reported.
    """

    sessions: int
    turns: int
    errors: int
    abstentions: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    total_latency_seconds: float
    max_rate_limit_utilisation: float | None

    @property
    def total_tokens(self) -> int:
        """Return the sum of input and output tokens."""
        return self.input_tokens + self.output_tokens

    @property
    def error_rate(self) -> float:
        """Return the fraction of turns that errored, or ``0.0`` before any turn."""
        return self.errors / self.turns if self.turns else 0.0

    @property
    def mean_latency_seconds(self) -> float:
        """Return the mean turn latency across all sessions, or ``0.0`` before any turn."""
        return self.total_latency_seconds / self.turns if self.turns else 0.0


@dataclass(frozen=True)
class SessionMetricReport:
    """Opt-in operational session telemetry built from a durable event store."""

    generated_from_seq: int
    as_of: float
    sessions: tuple[SessionMetricRecord, ...]
    totals: SessionMetricTotals

    @property
    def by_session(self) -> dict[tuple[str, str], SessionMetricRecord]:
        """Return session snapshots keyed by ``(agent, session_id)``."""
        return {(record.agent, record.session_id): record for record in self.sessions}


def run_session_metric_report(db_path: str | Path) -> SessionMetricReport:
    """Build a session-telemetry report from a hub SQLite event store.

    Parameters
    ----------
    db_path : str or pathlib.Path
        Path to a hub event-store database.

    Returns
    -------
    SessionMetricReport
        Aggregated opt-in operational session telemetry.

    Raises
    ------
    ValueError
        If the event store does not exist.
    """
    path = Path(db_path)
    if not path.exists():
        msg = f"missing event store: {path}"
        raise ValueError(msg)
    store = EventStore(path)
    try:
        events = tuple(store.read_all())
    finally:
        store.close()
    return build_session_metric_report(events)


def build_session_metric_report(events: Sequence[StoredEvent]) -> SessionMetricReport:
    """Build a session-telemetry report from loaded events.

    The latest snapshot per ``(agent, session)`` wins, because each snapshot is cumulative and
    supersedes its predecessors. Sessions are ordered by ``(agent, session_id)`` for a stable
    report; totals sum the surviving latest snapshots.

    Parameters
    ----------
    events : collections.abc.Sequence[StoredEvent]
        Durable events read from a hub event store.

    Returns
    -------
    SessionMetricReport
        Aggregated opt-in operational session telemetry.
    """
    latest: dict[tuple[str, str], SessionMetricRecord] = {}
    for record in _snapshot_records(events):
        key = (record.agent, record.session_id)
        current = latest.get(key)
        if current is None or record.seq >= current.seq:
            latest[key] = record
    sessions = tuple(latest[key] for key in sorted(latest))
    return SessionMetricReport(
        generated_from_seq=max((event.seq for event in events), default=0),
        as_of=max((event.ts for event in events), default=0.0),
        sessions=sessions,
        totals=_totals(sessions),
    )


def session_metric_report_to_json(report: SessionMetricReport) -> dict[str, object]:
    """Return a stable JSON-compatible representation of a session-telemetry report."""
    return {
        "generated_from_seq": report.generated_from_seq,
        "as_of": report.as_of,
        "totals": _totals_to_json(report.totals),
        "sessions": [_record_to_json(record) for record in report.sessions],
        "note": "opt-in operational telemetry, not hub-core collected or an enforcement gate",
    }


def render_session_metric_report(report: SessionMetricReport) -> str:
    """Render a session-telemetry report as compact terminal text."""
    header = "Session operational telemetry: opt-in evidence, not collected by the hub core"
    if not report.sessions:
        return f"{header}\n\nNo recorded session telemetry found."
    totals = report.totals
    lines = [
        header,
        f"generated_from_seq={report.generated_from_seq} as_of={report.as_of:.3f}",
        (
            f"totals: sessions={totals.sessions} turns={totals.turns} "
            f"errors={totals.errors} abstentions={totals.abstentions} "
            f"tokens={totals.total_tokens} (in={totals.input_tokens} out={totals.output_tokens}) "
            f"cost_usd={totals.cost_usd:.4f} mean_latency={totals.mean_latency_seconds:.3f}s "
            f"error_rate={totals.error_rate:.3f}{_utilisation_suffix(totals.max_rate_limit_utilisation)}"
        ),
        "",
        "By session",
    ]
    lines.extend(_render_record(record) for record in report.sessions)
    return "\n".join(lines)


def _snapshot_records(events: Sequence[StoredEvent]) -> tuple[SessionMetricRecord, ...]:
    """Return snapshot records parsed from ``kind="session_metric"`` progress notes."""
    records: list[SessionMetricRecord] = []
    for event in events:
        if event.kind != EventKind.LEDGER_PROGRESS:
            continue
        if str(event.payload.get("kind", "")) != SESSION_METRIC_NOTE_KIND:
            continue
        parsed = parse_session_metric_note(str(event.payload.get("text", "")))
        if parsed is None:
            continue
        records.append(
            SessionMetricRecord(
                agent=str(event.payload.get("author", "")),
                session_id=str(event.payload.get("task_id", "")),
                turns=int(parsed["turns"]),
                errors=int(parsed["errors"]),
                abstentions=int(parsed["abstentions"]),
                input_tokens=int(parsed["input_tokens"]),
                output_tokens=int(parsed["output_tokens"]),
                cost_usd=float(parsed["cost_usd"]),
                total_latency_seconds=float(parsed["total_latency_seconds"]),
                max_rate_limit_utilisation=parsed["max_rate_limit_utilisation"],
                last_input_tokens=int(parsed["last_input_tokens"]),
                seq=event.seq,
                ts=event.ts,
            )
        )
    return tuple(records)


def _totals(sessions: Sequence[SessionMetricRecord]) -> SessionMetricTotals:
    """Return fleet-wide totals across the surviving latest snapshots."""
    highest: float | None = None
    for record in sessions:
        highest = _higher(highest, record.max_rate_limit_utilisation)
    return SessionMetricTotals(
        sessions=len(sessions),
        turns=sum(record.turns for record in sessions),
        errors=sum(record.errors for record in sessions),
        abstentions=sum(record.abstentions for record in sessions),
        input_tokens=sum(record.input_tokens for record in sessions),
        output_tokens=sum(record.output_tokens for record in sessions),
        cost_usd=sum(record.cost_usd for record in sessions),
        total_latency_seconds=sum(record.total_latency_seconds for record in sessions),
        max_rate_limit_utilisation=highest,
    )


def _higher(current: float | None, observed: float | None) -> float | None:
    """Return the greater of two optional utilisations, ignoring a missing one."""
    if observed is None:
        return current
    if current is None:
        return observed
    return max(current, observed)


def _record_to_json(record: SessionMetricRecord) -> dict[str, object]:
    """Convert a session snapshot into JSON-compatible fields."""
    return {
        "agent": record.agent,
        "session_id": record.session_id,
        "turns": record.turns,
        "errors": record.errors,
        "abstentions": record.abstentions,
        "input_tokens": record.input_tokens,
        "output_tokens": record.output_tokens,
        "total_tokens": record.total_tokens,
        "cost_usd": record.cost_usd,
        "total_latency_seconds": record.total_latency_seconds,
        "mean_latency_seconds": record.mean_latency_seconds,
        "error_rate": record.error_rate,
        "max_rate_limit_utilisation": record.max_rate_limit_utilisation,
        "last_input_tokens": record.last_input_tokens,
        "seq": record.seq,
        "ts": record.ts,
    }


def _totals_to_json(totals: SessionMetricTotals) -> dict[str, object]:
    """Convert fleet totals into JSON-compatible fields."""
    return {
        "sessions": totals.sessions,
        "turns": totals.turns,
        "errors": totals.errors,
        "abstentions": totals.abstentions,
        "input_tokens": totals.input_tokens,
        "output_tokens": totals.output_tokens,
        "total_tokens": totals.total_tokens,
        "cost_usd": totals.cost_usd,
        "total_latency_seconds": totals.total_latency_seconds,
        "mean_latency_seconds": totals.mean_latency_seconds,
        "error_rate": totals.error_rate,
        "max_rate_limit_utilisation": totals.max_rate_limit_utilisation,
    }


def _render_record(record: SessionMetricRecord) -> str:
    """Render one session snapshot row."""
    agent = record.agent or "(unattributed)"
    session = record.session_id or "(no-session)"
    return (
        f"- {agent}/{session}: turns={record.turns} errors={record.errors} "
        f"abstentions={record.abstentions} tokens={record.total_tokens} "
        f"(in={record.input_tokens} out={record.output_tokens}) cost_usd={record.cost_usd:.4f} "
        f"mean_latency={record.mean_latency_seconds:.3f}s ctx={record.last_input_tokens}"
        f"{_utilisation_suffix(record.max_rate_limit_utilisation)}"
    )


def _utilisation_suffix(utilisation: float | None) -> str:
    """Return a trailing rate-limit utilisation fragment, or empty when none was seen."""
    return "" if utilisation is None else f" max_rate_limit={utilisation:.3f}"
