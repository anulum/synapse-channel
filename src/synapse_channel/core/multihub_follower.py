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

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from synapse_channel.core.clock_skew import ClockSkew
from synapse_channel.core.journal import (
    record_multihub_equivocation,
    record_multihub_equivocation_recovery,
    restore_active_multihub_quarantines,
)
from synapse_channel.core.multihub_equivocation import (
    FederationQuarantine,
    MultiHubEquivocationError,
    MultiHubPeerQuarantinedError,
    validate_content_bound_batch,
)
from synapse_channel.core.multihub_fold import (
    ObservedState,
    asserting_owners_from_events,
    fold_observed_state,
)
from synapse_channel.core.multihub_merge import HubEvent, hub_cursors, merge_event_logs, tag_events
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.protocol import ProtocolNegotiation

EventFetcher = Callable[[int], Awaitable[Sequence[StoredEvent]]]
"""Fetch a peer's events with ``seq`` greater than a cursor — the injected transport."""


class ProtocolAwareEventFetcher(Protocol):
    """Optional fetcher metadata surface for peer transport observations."""

    last_protocol_negotiation: ProtocolNegotiation | None
    """Most recent peer wire-version negotiation observed by the fetcher."""

    last_clock_skew: ClockSkew | None
    """Most recent peer clock-skew observation exposed by the fetcher."""


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

    def __init__(
        self,
        *,
        journal: EventStore | None = None,
        observer_id: str = "",
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._events: dict[tuple[str, int], HubEvent] = {}
        self._cursors: dict[str, int] = {}
        self._protocol_negotiations: dict[str, ProtocolNegotiation] = {}
        self._clock_skews: dict[str, ClockSkew] = {}
        self._journal = journal
        self._observer_id = observer_id
        self._clock = clock
        self._quarantines = (
            restore_active_multihub_quarantines(journal) if journal is not None else {}
        )
        self._peer_locks: dict[str, asyncio.Lock] = {}

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
        lock = self._peer_locks.setdefault(peer_id, asyncio.Lock())
        async with lock:
            quarantine = self._quarantines.get(peer_id)
            if quarantine is not None:
                raise MultiHubPeerQuarantinedError(quarantine)
            cursor = self._cursors.get(peer_id, 0)
            fetched = await fetch(cursor)
            candidate = dict(self._events)
            try:
                validate_content_bound_batch(
                    candidate,
                    tag_events(peer_id, fetched),
                    peer_id=peer_id,
                    after_seq=cursor,
                )
            except MultiHubEquivocationError as exc:
                try:
                    self._quarantine(exc)
                except Exception as evidence_error:
                    # Evidence failure must not downgrade the stable integrity
                    # result or allow another automatic poll in this process.
                    raise exc from evidence_error
                raise

            observed = fold_observed_state(merge_event_logs(candidate.values()))
            cursors = hub_cursors(candidate.values())
            negotiation = _fetcher_protocol_negotiation(fetch)
            if negotiation is not None:
                self._protocol_negotiations[peer_id] = negotiation
            skew = _fetcher_clock_skew(fetch)
            if skew is not None:
                self._clock_skews[peer_id] = skew
            self._events = candidate
            self._cursors = cursors
            return observed

    def observed(self) -> ObservedState:
        """Return the observed state folded from the full accumulated union."""
        return fold_observed_state(merge_event_logs(self._events.values()))

    def asserting_owners(self, *, project_of: Callable[[str], str]) -> dict[str, frozenset[str]]:
        """Return the per-hub live-claim authority signal for partition detection."""
        return asserting_owners_from_events(self._events.values(), project_of=project_of)

    def cursor(self, peer_id: str) -> int:
        """Return the highest ``seq`` consumed for ``peer_id`` (``0`` if never polled)."""
        return self._cursors.get(peer_id, 0)

    def peers(self) -> tuple[str, ...]:
        """Return the peer hub ids the follower has observed, sorted."""
        return tuple(sorted(self._cursors))

    def protocol_negotiation(self, peer_id: str) -> ProtocolNegotiation | None:
        """Return the last wire-version negotiation observed for ``peer_id``."""
        return self._protocol_negotiations.get(peer_id)

    def clock_skew(self, peer_id: str) -> ClockSkew | None:
        """Return the last local-minus-peer clock skew observed for ``peer_id``."""
        return self._clock_skews.get(peer_id)

    def quarantines(self) -> tuple[FederationQuarantine, ...]:
        """Return active peer quarantines in peer-id order."""
        return tuple(self._quarantines[peer] for peer in sorted(self._quarantines))

    def recover_peer(
        self,
        peer_id: str,
        *,
        recovered_by: str,
        reason: str,
        new_log_generation: str,
    ) -> None:
        """Explicitly clear one quarantine and reset that peer's observed history.

        Recovery requires a named operator, a reason, and a new log-generation or
        checkpoint identity. In durable mode the recovery record commits before
        in-memory state is cleared; reconnecting or restarting alone never heals it.
        """
        if peer_id not in self._quarantines:
            raise ValueError(f"peer {peer_id!r} is not quarantined")
        for label, value in (
            ("recovered_by", recovered_by),
            ("reason", reason),
            ("new_log_generation", new_log_generation),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"multi-hub recovery requires a non-empty {label}")
        recovered_at = self._clock()
        if self._journal is not None:
            record_multihub_equivocation_recovery(
                self._journal,
                peer_id=peer_id,
                recovered_at=recovered_at,
                recovered_by=recovered_by,
                reason=reason,
                new_log_generation=new_log_generation,
            )
        self._events = {
            identity: event for identity, event in self._events.items() if event.hub_id != peer_id
        }
        self._cursors.pop(peer_id, None)
        self._protocol_negotiations.pop(peer_id, None)
        self._clock_skews.pop(peer_id, None)
        self._quarantines.pop(peer_id, None)

    def _quarantine(self, conflict: MultiHubEquivocationError) -> None:
        """Freeze one peer and durably record bounded digest-only evidence."""
        quarantine = FederationQuarantine(
            peer_id=conflict.peer_id,
            seq=conflict.seq,
            accepted_fingerprint=conflict.accepted_fingerprint,
            conflicting_fingerprint=conflict.conflicting_fingerprint,
            detected_at=self._clock(),
            observer_id=self._observer_id,
        )
        self._quarantines[conflict.peer_id] = quarantine
        if self._journal is not None:
            record_multihub_equivocation(self._journal, quarantine)


def _fetcher_protocol_negotiation(fetch: EventFetcher) -> ProtocolNegotiation | None:
    """Return optional protocol metadata exposed by a network fetcher."""
    candidate = getattr(fetch, "last_protocol_negotiation", None)
    if isinstance(candidate, ProtocolNegotiation):
        return candidate
    return None


def _fetcher_clock_skew(fetch: EventFetcher) -> ClockSkew | None:
    """Return optional clock-skew metadata exposed by a network fetcher."""
    candidate = getattr(fetch, "last_clock_skew", None)
    if isinstance(candidate, ClockSkew):
        return candidate
    return None
