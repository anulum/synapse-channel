# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — content-minimized local fleet health projection
"""Summarise local contention, lease expiry, and dead-letter evidence.

The projection emits counts and retention boundaries only. It never returns
agent identities, task ids, paths, messages, notes, or event payloads, and it
never opens a network connection. Results are computed on demand from the
events currently retained in one local hub store; the report creates no second
retention surface.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.causality import DONE_STATUSES, GRAPH_KINDS, build_causal_graph
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.replay import SNAPSHOT_KINDS
from synapse_channel.core.yield_advice import advise_yields

POLICY_VERSION = 1
"""Version of the report's minimisation and retention contract."""


@dataclass(frozen=True)
class LocalFleetHealth:
    """Content-minimized health summary for one retained local event log."""

    generated_at: float
    first_retained_seq: int
    generated_from_seq: int
    retained_events: int
    contention_pairs: int
    expired_claims: int
    dead_lettered_messages: int
    recovered_messages: int
    dead_letter_escalations: int
    level: str


def build_local_fleet_health(
    events: Sequence[StoredEvent], *, generated_at: float
) -> LocalFleetHealth:
    """Build a minimized local fleet-health report from retained events.

    Parameters
    ----------
    events : Sequence[StoredEvent]
        Events currently retained in one local hub store.
    generated_at : float
        Finite POSIX timestamp used to assess recorded lease expiry.

    Returns
    -------
    LocalFleetHealth
        Aggregate counts without participant or work-content fields.

    Raises
    ------
    ValueError
        If ``generated_at`` is not finite.
    """
    if not math.isfinite(generated_at):
        msg = "generated_at must be finite"
        raise ValueError(msg)
    ordered = sorted(events, key=lambda event: event.seq)
    graph = build_causal_graph([event for event in ordered if event.kind in GRAPH_KINDS])
    contention_pairs = len(advise_yields(graph))
    expired_claims = _expired_claim_count(ordered, generated_at)
    dead_lettered = sum(
        event.kind == EventKind.DELIVERY_RECEIPT_IMMEDIATE
        and event.payload.get("dead_lettered") is True
        for event in ordered
    )
    recovered = sum(event.kind == EventKind.DELIVERY_RECEIPT_DEFERRED for event in ordered)
    escalations = sum(event.kind == EventKind.DEAD_LETTER_ESCALATION for event in ordered)
    if expired_claims or escalations:
        level = "red"
    elif contention_pairs or dead_lettered:
        level = "amber"
    else:
        level = "green"
    return LocalFleetHealth(
        generated_at=generated_at,
        first_retained_seq=ordered[0].seq if ordered else 0,
        generated_from_seq=ordered[-1].seq if ordered else 0,
        retained_events=len(ordered),
        contention_pairs=contention_pairs,
        expired_claims=expired_claims,
        dead_lettered_messages=dead_lettered,
        recovered_messages=recovered,
        dead_letter_escalations=escalations,
        level=level,
    )


def run_local_fleet_health(
    db_path: str | Path,
    *,
    generated_at: float | None = None,
    key_file: str | Path | None = None,
) -> LocalFleetHealth:
    """Read one local event store and return its minimized health summary.

    Parameters
    ----------
    db_path : str or pathlib.Path
        Existing local hub event-store path.
    generated_at : float or None, optional
        Lease-expiry clock. The final retained event timestamp is used when
        omitted, keeping the report reproducible from the same store.
    key_file : str or pathlib.Path or None, optional
        Owner-only SQLCipher key for an encrypted store.

    Returns
    -------
    LocalFleetHealth
        Aggregate local report.

    Raises
    ------
    ValueError
        If the event store is missing or the timestamp is invalid.
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
    effective_at = (
        max((event.ts for event in events), default=0.0) if generated_at is None else generated_at
    )
    return build_local_fleet_health(events, generated_at=effective_at)


def local_fleet_health_to_json(report: LocalFleetHealth) -> dict[str, object]:
    """Return the stable public JSON representation of a local report."""
    return {
        "policy_version": POLICY_VERSION,
        "level": report.level,
        "generated_at": report.generated_at,
        "first_retained_seq": report.first_retained_seq,
        "generated_from_seq": report.generated_from_seq,
        "retained_events": report.retained_events,
        "contention_pairs": report.contention_pairs,
        "expired_claims": report.expired_claims,
        "dead_lettered_messages": report.dead_lettered_messages,
        "recovered_messages": report.recovered_messages,
        "dead_letter_escalations": report.dead_letter_escalations,
        "retention": "computed from the current local retained log; report not persisted",
        "redaction": (
            "counts only; identities, task ids, paths, messages, notes, and payloads omitted"
        ),
        "telemetry": "none",
    }


def _expired_claim_count(events: Sequence[StoredEvent], generated_at: float) -> int:
    """Count latest unreleased claim snapshots whose recorded lease elapsed."""
    live: dict[str, StoredEvent] = {}
    for event in events:
        task_id = str(event.payload.get("task_id") or "")
        if not task_id:
            continue
        if event.kind == EventKind.RELEASE:
            live.pop(task_id, None)
            continue
        if event.kind not in SNAPSHOT_KINDS:
            continue
        if str(event.payload.get("status") or "") in DONE_STATUSES:
            live.pop(task_id, None)
            continue
        if str(event.payload.get("owner") or ""):
            live[task_id] = event
    count = 0
    for event in live.values():
        raw_expiry = event.payload.get("lease_expires_at")
        if isinstance(raw_expiry, bool) or not isinstance(raw_expiry, (int, float)):
            continue
        if math.isfinite(float(raw_expiry)) and float(raw_expiry) <= generated_at:
            count += 1
    return count
