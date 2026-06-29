# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the read-only multi-hub follower over the seq-cursored ingest seam
"""A read-only follower that observes peer hubs over the seq-cursored ingest seam.

This is the third multi-hub slice (`docs/multi-hub-sync.md`), joining the event-log
union (:mod:`synapse_channel.core.multihub_merge`) and the observed-state fold
(:mod:`synapse_channel.core.multihub_fold`). A :class:`MultiHubFollower` tracks, per
peer hub, the highest ``seq`` it has consumed; each poll fetches that peer's events
beyond the cursor, tags them with the peer's hub id, folds them into the running union,
and returns the merged :class:`~synapse_channel.core.multihub_fold.ObservedState`. The
fetch is injected, so the transport is pluggable — :func:`store_fetcher` reads a peer
:class:`~synapse_channel.core.persistence.EventStore` through its ``read_since`` cursor
(the seq-cursored ingest seam the persistent-memory read-side already uses), and a
network transport would slot in the same way.

The follower is **read-only and observe-only by construction**. It folds a peer's log
into an *observed* view and never grants a claim — claims are mutual exclusion, owned
by a single hub per namespace, so a real claim request is routed to the owning hub, not
satisfied from this view. A follower that loses a peer simply stops advancing that
peer's cursor; it keeps serving the last observed view and never invents authority,
which is the fail-closed posture the design requires.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

from synapse_channel.core.multihub_fold import ObservedState, fold_observed_state
from synapse_channel.core.multihub_merge import HubEvent, hub_cursors, merge_event_logs, tag_events
from synapse_channel.core.persistence import EventStore, StoredEvent

EventFetcher = Callable[[int], Awaitable[Sequence[StoredEvent]]]
"""Fetch a peer's events with ``seq`` greater than a cursor — the injected transport."""


def store_fetcher(store: EventStore) -> EventFetcher:
    """Return a fetcher that reads a peer :class:`EventStore` over its ``read_since`` seam."""

    async def fetch(after_seq: int) -> Sequence[StoredEvent]:
        return store.read_since(after_seq)

    return fetch


class MultiHubFollower:
    """Track per-peer cursors and fold peer logs into one observed view.

    The follower accumulates the union of every peer event it has seen, keyed by
    ``(hub_id, seq)`` so a re-fetch is idempotent, and re-derives the observed state
    from the full union on each poll (deterministic regardless of arrival order).
    """

    def __init__(self) -> None:
        self._events: dict[tuple[str, int], HubEvent] = {}
        self._cursors: dict[str, int] = {}

    async def poll(self, peer_id: str, fetch: EventFetcher) -> ObservedState:
        """Fetch a peer's new events past its cursor, fold the union, and return the view.

        Parameters
        ----------
        peer_id : str
            Id of the peer hub being polled; its events are tagged with it.
        fetch : EventFetcher
            Transport that returns the peer's events with ``seq`` above a cursor.

        Returns
        -------
        ObservedState
            The merged observed view across every peer polled so far.
        """
        fetched = await fetch(self._cursors.get(peer_id, 0))
        for event in tag_events(peer_id, fetched):
            self._events.setdefault(event.identity, event)
        self._cursors.update(hub_cursors(self._events.values()))
        return self.observed()

    def observed(self) -> ObservedState:
        """Return the observed state folded from the full accumulated union."""
        return fold_observed_state(merge_event_logs(self._events.values()))

    def cursor(self, peer_id: str) -> int:
        """Return the highest ``seq`` consumed for ``peer_id`` (``0`` if never polled)."""
        return self._cursors.get(peer_id, 0)

    def peers(self) -> tuple[str, ...]:
        """Return the peer hub ids the follower has observed, sorted."""
        return tuple(sorted(self._cursors))
