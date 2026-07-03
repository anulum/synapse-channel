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
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

from synapse_channel.core.causality import DEFAULT_MAX_GRAPH_NODES, causality_to_json, run_causality
from synapse_channel.core.federation_store import load_store
from synapse_channel.core.federation_wire import bundle_fingerprint
from synapse_channel.core.multihub_wire import LogSnapshot, encode_log_snapshot
from synapse_channel.core.persistence import EventStore

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
    return causality_to_json(query)


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
