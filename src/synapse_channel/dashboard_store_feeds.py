# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard JSON feeds built from the durable stores
"""Build the dashboard's store-backed JSON feeds for cockpit clients.

The dashboard's live snapshot rides the hub connection; these feeds ride the
**durable stores** instead — the hub event log and the operator's federation
store — so they answer with real sequences and timestamps, stay available when
the hub is down, and never invent state the disk cannot prove. Three feeds:

- **events tail** — the raw event log past a cursor, in the exact
  ``multihub_wire`` snapshot shape (one wire encoding for stored events,
  whether a peer hub pulls them or a cockpit polls them);
- **causality** — one causality query rendered by the same
  ``causality_to_json`` the CLI emits, with an optional task-id resolver so a
  client can hop from a log row to its causal cone without knowing sequences;
- **federation** — the imported peerings with their provenance and bundle
  fingerprints. Namespace outcomes (local/remote/ungoverned/partitioned) are
  hub-runtime state that no durable store carries, so the section ships empty
  with its absence stated rather than guessed.

Six further store-derived feeds serve the cockpit's operational panels, all
measured against the log's own timestamps (never the wall clock) so each is
deterministic and available with the hub down: **metrics** (event counts by
kind over trailing windows), **state-at** (coordination state reconstructed by
bounded replay to a sequence — whole-fleet time-travel), **merkle proof** (an
inclusion proof for one event so a cockpit row can be verified against the
attested tree root), **health anomalies** (orphaned, dangling, and stale
coordination signals the causality graph makes visible — the honest hub-side
alert surface), **sessions** (the opt-in ``session_metric`` telemetry the fleet
left in the log, each record indexed by ``seq`` for a cost-to-causality join),
and **waits** (the pending coordination gates — non-terminal tasks blocked on
dependencies that have not completed — reconstructed from the plan).
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

from synapse_channel.core.causality import DEFAULT_MAX_GRAPH_NODES, causality_to_json, run_causality
from synapse_channel.core.causality_health import (
    DEFAULT_STALE_AFTER,
    health_to_json,
    run_causal_health,
)
from synapse_channel.core.federation_store import load_store
from synapse_channel.core.federation_wire import bundle_fingerprint
from synapse_channel.core.journal import replay
from synapse_channel.core.ledger import TERMINAL_LEDGER_STATUSES
from synapse_channel.core.merkle import proof_to_json, run_proof
from synapse_channel.core.multihub_wire import LogSnapshot, encode_log_snapshot
from synapse_channel.core.persistence import EventStore
from synapse_channel.participants.session_metric_report import (
    run_session_metric_report,
    session_metric_report_to_json,
)

Clock = Callable[[], float]

DEFAULT_EVENTS_LIMIT = 200
"""Events returned per tail request when the client names no limit."""

MAX_EVENTS_LIMIT = 1000
"""Hard ceiling per tail request — a cockpit polls forward, it never bulk-dumps."""

CAUSALITY_FEED_DIRECTIONS = ("causes", "effects")
"""Query directions the feed answers; the CLI's other modes stay CLI-only."""


def build_events_tail(
    db_path: str | Path, *, since: int = 0, limit: int = DEFAULT_EVENTS_LIMIT
) -> dict[str, object]:
    """Return the event log past a cursor in the multihub snapshot shape.

    Parameters
    ----------
    db_path : str or pathlib.Path
        The hub event store.
    since : int
        Exclusive sequence cursor; ``0`` starts at the log's beginning.
    limit : int
        Batch cap, clamped to ``1..MAX_EVENTS_LIMIT``.

    Returns
    -------
    dict[str, object]
        ``encode_log_snapshot`` output: ``events`` (each with real ``seq``,
        ``ts``, ``kind``, ``payload``) and ``next_cursor`` for the next poll.

    Raises
    ------
    ValueError
        If the store does not exist.
    """
    path = Path(db_path)
    if not path.exists():
        msg = f"missing event store: {path}"
        raise ValueError(msg)
    bounded = max(1, min(int(limit), MAX_EVENTS_LIMIT))
    store = EventStore(path)
    try:
        events = store.read_since(max(0, int(since)), limit=bounded)
    finally:
        store.close()
    next_cursor = events[-1].seq if events else max(0, int(since))
    return encode_log_snapshot(LogSnapshot(events=tuple(events), next_cursor=next_cursor))


METRIC_WINDOWS_SECONDS = {"last_hour": 3600.0, "last_day": 86400.0}
"""Log-relative aggregation windows the metrics feed reports."""


def build_state_at_feed(db_path: str | Path, *, seq: int) -> dict[str, object]:
    """Reconstruct coordination state as of event ``seq`` by bounded replay.

    Replays the durable event store up to and including ``seq`` and returns the
    reconstructed state and board in the same shape the live hub's state and
    board snapshots use, plus ``as_of_seq`` and ``log_end_seq`` so a caller
    scrubbing a timeline knows where it is. Store-derived and deterministic —
    the same log and seq always rebuild the same state — so a cockpit can
    time-travel the whole fleet's claims and board, not just the event log.

    Honest scope: **presence/roster is not in the durable log** (live socket
    connections are not journalled), so this feed reconstructs *claims and the
    board*, not who was online. ``seq`` is clamped into ``0..log_end_seq``; a
    seq at or past the end yields the current reconstructed state.

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
        log_end_seq = store.max_seq()
        bounded = max(0, min(int(seq), log_end_seq))
        # Reconstruct as of the bounded event's own timestamp, never the wall
        # clock — so lease expiry is judged at that point in time and the
        # document is deterministic (a lease live at seq N reads live).
        as_of_ts = next(
            (event.ts for event in reversed(store.read_since(0)) if event.seq <= bounded),
            None,
        )
        result = replay(EventStore(path), up_to_seq=bounded, now=as_of_ts)
    finally:
        store.close()
    snapshot_now = as_of_ts if as_of_ts is not None else 0.0
    return {
        "as_of_seq": bounded,
        "log_end_seq": log_end_seq,
        "state": result.state.snapshot(now=snapshot_now),
        "board": result.blackboard.snapshot(),
        "note": (
            "claims and board reconstructed from the durable log up to as_of_seq; "
            "live presence/roster is not journalled and is omitted"
        ),
    }


def build_waits_feed(db_path: str | Path) -> dict[str, object]:
    """Report the coordination gates the plan is currently waiting on.

    Reconstructs the board from the durable log and lists the pending gates: each
    non-terminal task whose declared dependencies have not all reached a terminal
    status, with **who** is waiting (the task's suggested owner, or whoever
    declared it), **on** which dependency ids it is blocked, and **since** when it
    was declared. This is the "what is the fleet stuck behind" panel — the gates
    an operator clears by finishing a prerequisite.

    Store-derived and deterministic like every store feed: the same log rebuilds
    the same gates, dependency satisfaction is judged from the log's own recorded
    task statuses, and it answers with the hub down. Honest scope: live socket
    waiters (a client's ``-rx`` connection parked on the bus) are transient hub
    state, never journalled, so they are omitted — this is the *coordination*
    gates the durable plan can prove, not who currently holds a socket open.

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
        log_end_seq = store.max_seq()
        as_of_ts = next((event.ts for event in reversed(store.read_since(0))), None)
        result = replay(EventStore(path), up_to_seq=log_end_seq, now=as_of_ts)
    finally:
        store.close()
    board = result.blackboard
    waits: list[dict[str, object]] = []
    for task in sorted(board.tasks.values(), key=lambda item: item.task_id):
        if task.status in TERMINAL_LEDGER_STATUSES:
            continue
        blocking = board.blocking_dependencies(task.task_id)
        if not blocking:
            continue
        waits.append(
            {
                "task_id": task.task_id,
                "title": task.title,
                "who": task.suggested_owner or task.created_by,
                "on_what": blocking,
                "since": task.created_at,
                "status": task.status,
            }
        )
    return {
        "present": True,
        "waits": waits,
        "wait_count": len(waits),
        "log_end_seq": log_end_seq,
        "note": (
            "pending coordination gates reconstructed from the durable log: "
            "non-terminal tasks whose declared dependencies have not reached a "
            "terminal status; transient socket waiters are not journalled and "
            "are omitted"
        ),
    }


def build_metrics_feed(db_path: str | Path) -> dict[str, object]:
    """Aggregate the event store into operational metrics for the cockpit.

    Store-attested log metrics — total and per-kind event counts, plus the
    same split over trailing windows — measured against the log's own final
    timestamp, never the wall clock, so the document is deterministic over a
    given log and replayable byte-for-byte (the causality-health doctrine).
    Available with the hub down, like every store feed. Honest scope: these
    are *log* metrics; the live process's Prometheus registry (connection
    gauges, handler timings) is served by the hub's own ``/metrics`` endpoint
    and is deliberately not duplicated here — the ``note`` says so.

    Raises
    ------
    ValueError
        If the store does not exist.
    """
    path = Path(db_path)
    if not path.exists():
        msg = f"missing event store: {path}"
        raise ValueError(msg)
    connection = sqlite3.connect(path)
    try:
        total, first_ts, last_ts, max_seq = connection.execute(
            "SELECT COUNT(*), MIN(ts), MAX(ts), MAX(seq) FROM events"
        ).fetchone()
        by_kind = dict(
            connection.execute("SELECT kind, COUNT(*) FROM events GROUP BY kind ORDER BY kind")
        )
        windows: dict[str, object] = {}
        for name, span in METRIC_WINDOWS_SECONDS.items():
            cutoff = float(last_ts) - span if last_ts is not None else 0.0
            window_kinds = dict(
                connection.execute(
                    "SELECT kind, COUNT(*) FROM events WHERE ts >= ? GROUP BY kind ORDER BY kind",
                    (cutoff,),
                )
            )
            windows[name] = {
                "events": sum(window_kinds.values()),
                "by_kind": window_kinds,
            }
    finally:
        connection.close()
    return {
        "source": "event-store",
        "log": {
            "total_events": int(total),
            "max_seq": int(max_seq) if max_seq is not None else 0,
            "first_ts": float(first_ts) if first_ts is not None else None,
            "last_ts": float(last_ts) if last_ts is not None else None,
        },
        "events_by_kind": by_kind,
        "windows": windows,
        "note": (
            "log metrics measured against the log's final timestamp; the live "
            "process registry is the hub's own /metrics endpoint"
        ),
    }


def build_merkle_proof_feed(db_path: str | Path, *, seq: int) -> dict[str, object]:
    """Prove a single event's inclusion in the attested log by its sequence.

    Builds an RFC 6962 Merkle inclusion proof for event ``seq`` against the
    durable log and returns it in the same JSON shape ``synapse debug merkle``
    emits, so a cockpit's per-row *verify* button can hand the proof straight to
    the client-side :func:`~synapse_channel.core.merkle.verify_inclusion` (via
    ``proof_from_json``) and confirm the row is committed to the tree root —
    tamper-evidence the operator can check without trusting the dashboard.

    Store-derived and deterministic: the same log and ``seq`` always yield the
    same proof (the tree is built over the committed leaves, not the wall
    clock), and it answers with the hub down like every store feed. Honest
    scope: a ``seq`` with no event in the committed log returns
    ``{"present": False}`` with a note rather than a fabricated proof — the
    tree can only attest sequences it actually holds.

    Raises
    ------
    ValueError
        If the event store does not exist.
    """
    path = Path(db_path)
    if not path.exists():
        msg = f"missing event store: {path}"
        raise ValueError(msg)
    proof = run_proof(path, seq)
    if proof is None:
        return {
            "present": False,
            "seq": seq,
            "note": "no event at that sequence in the committed log",
        }
    return {"present": True, **proof_to_json(proof)}


def build_sessions_feed(db_path: str | Path) -> dict[str, object]:
    """Report opt-in session telemetry the fleet left in the durable log.

    Aggregates the ``session_metric`` progress notes participants emit (turns,
    token counts, cost, latency, error and abstention rates) into the same JSON
    the ``synapse participants costs`` report renders, so a cockpit can join a
    session's cost to its causal cone: every per-session record carries the
    ``seq`` of the snapshot it was read from, which indexes straight back into
    the event log the causality feed walks.

    Store-derived and deterministic like every store feed — the latest snapshot
    per ``(agent, session)`` wins because each snapshot is cumulative, and the
    report answers with the hub down. Honest scope: this is opt-in operational
    telemetry, never hub-core collected and never an enforcement gate; a log
    with no ``session_metric`` notes yields empty ``sessions`` and zeroed
    ``totals`` rather than a fabricated cost.

    Raises
    ------
    ValueError
        If the event store does not exist.
    """
    return session_metric_report_to_json(run_session_metric_report(db_path))


def build_health_anomalies_feed(
    db_path: str | Path, *, stale_after: float = DEFAULT_STALE_AFTER
) -> dict[str, object]:
    """Flag coordination anomalies the causality graph makes visible.

    Assesses the durable log for the three anomaly shapes ``synapse causality
    --health`` reports — orphaned claims (a claim is its task's final recorded
    event), dangling dependencies (a declared dependency that never completed),
    and stale claims (unreleased and silent past ``stale_after``) — and returns
    them in the same JSON shape that CLI emits, plus an ``anomaly_count`` a
    cockpit's alerts badge can show.

    This is the honest hub-side "alert" surface: fired alerts live collector
    side (the hub's ``/metrics`` feeds Prometheus/Alertmanager), but the
    coordination anomalies the log can *prove* belong here — store-derived,
    deterministic (every age measured against the log's own final timestamp,
    never the wall clock), and available with the hub down like every store
    feed.

    Raises
    ------
    ValueError
        If the event store does not exist or exceeds the graph node ceiling.
    """
    report = run_causal_health(db_path, stale_after=stale_after)
    return {"present": True, **health_to_json(report)}


def latest_cursor(db_path: str | Path) -> int:
    """Return the log's highest sequence — the ``since=latest`` tail shortcut.

    A client that only wants "now onwards" on a large log starts here
    instead of walking the whole history to catch up.

    Raises
    ------
    ValueError
        If the store does not exist.
    """
    path = Path(db_path)
    if not path.exists():
        msg = f"missing event store: {path}"
        raise ValueError(msg)
    store = EventStore(path)
    try:
        return store.max_seq()
    finally:
        store.close()


def _seq_exists(db_path: str | Path, seq: int) -> bool:
    """Return whether the log records an event at exactly ``seq``."""
    store = EventStore(Path(db_path))
    try:
        batch = store.read_since(max(0, seq - 1), limit=1)
    finally:
        store.close()
    return bool(batch) and batch[0].seq == seq


def resolve_task_last_seq(db_path: str | Path, task_id: str) -> int | None:
    """Return the sequence of a task's most recent recorded event, or ``None``.

    One forward walk of the log keeping the last match — the resolver a
    cockpit uses to hop from a task name to its causal cone. A task the log
    never recorded resolves to ``None`` rather than an invented sequence.

    Raises
    ------
    ValueError
        If the store does not exist.
    """
    path = Path(db_path)
    if not path.exists():
        msg = f"missing event store: {path}"
        raise ValueError(msg)
    last: int | None = None
    store = EventStore(path)
    try:
        for event in store.read_since(0):
            if event.payload.get("task_id") == task_id:
                last = event.seq
    finally:
        store.close()
    return last


def build_causality_feed(
    db_path: str | Path,
    *,
    direction: str,
    seq: int | None = None,
    task: str | None = None,
    max_nodes: int = DEFAULT_MAX_GRAPH_NODES,
) -> dict[str, object]:
    """Answer one causality query in the CLI's exact JSON shape.

    Exactly one of ``seq`` and ``task`` selects the anchor event; ``task``
    resolves to the task's most recent recorded event first, so a client can
    hop from a log row without knowing sequences.

    Raises
    ------
    ValueError
        On an unknown direction, a missing store, both or neither anchor
        given, or a task the log does not record.
    """
    if direction not in CAUSALITY_FEED_DIRECTIONS:
        msg = f"unknown causality direction '{direction}'; expected one of "
        msg += "/".join(CAUSALITY_FEED_DIRECTIONS)
        raise ValueError(msg)
    if (seq is None) == (task is None):
        msg = "exactly one of seq and task selects the anchor event"
        raise ValueError(msg)
    if task is not None:
        resolved = resolve_task_last_seq(db_path, task)
        if resolved is None:
            msg = f"no recorded event for task '{task}'"
            raise ValueError(msg)
        anchor = resolved
    else:
        # the exclusive-anchor guard leaves seq non-None on this branch
        anchor = int(cast("int", seq))
    query = run_causality(db_path, direction, anchor, max_nodes=max_nodes)
    document = causality_to_json(query)
    if document.get("present") is False:
        # `present` means "in the coordination causal graph", not "in the
        # log" — a chat frame is recorded but carries no causal edges. Say
        # which of the two the client is looking at instead of letting
        # `false` read as "nothing there".
        document["note"] = (
            "event recorded but outside the coordination causal graph "
            "(chatter and taskless events carry no causal edges)"
            if _seq_exists(db_path, anchor)
            else "no event recorded at this sequence"
        )
    return document


def build_federation_feed(store_path: str | Path, *, clock: Clock = time.time) -> dict[str, object]:
    """Return the imported peerings with provenance and bundle fingerprints.

    Each peering carries the state the durable store proves — ``revoked``
    beats ``expired`` beats ``active`` — its confirmed provenance, and the
    whole-bundle fingerprint operators compared in the exchange ceremony.
    Namespace outcomes are hub-runtime state no durable store carries, so
    ``namespaces`` ships empty and the note says why.

    Raises
    ------
    FederationStoreError
        If the store exists but cannot be parsed.
    """
    records = load_store(Path(store_path))
    now = clock()
    peerings = []
    for domain_id in sorted(records):
        record = records[domain_id]
        peer = record.peer
        if peer.revoked:
            state = "revoked"
        elif peer.expires_at is not None and now >= peer.expires_at:
            state = "expired"
        else:
            state = "active"
        peerings.append(
            {
                "domain": domain_id,
                "state": state,
                "imported_at": record.provenance.imported_at,
                "confirmed_by": record.provenance.confirmed_by,
                "source": record.provenance.source,
                "fingerprint": bundle_fingerprint(peer),
                "expires_at": peer.expires_at,
            }
        )
    return {
        "peerings": peerings,
        "namespaces": [],
        "note": (
            "peerings from the durable federation store; namespace outcomes "
            "are hub-runtime state and are not served here"
        ),
    }
