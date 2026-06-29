# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the CRDT-shaped event-log union for multi-hub sync
"""The conflict-free event-log union at the heart of multi-hub sync.

A single hub's durable log is append-only with a local monotonic ``seq``. Across
hubs the design (`docs/multi-hub-sync.md`) makes the log the one genuinely
CRDT-shaped piece of coordination state: tag every event with the id of the hub that
authored it, and the union of several hubs' logs is a **grow-only set** keyed by
``(hub_id, seq)``. Merging is set union with that key, and replaying the merged log
in a deterministic total order — ``(ts, hub_id, seq)`` — reproduces the same folded
state on every peer, regardless of the order logs arrived in.

This module is exactly that union and nothing more: a :class:`HubEvent` (a stored
event tagged with its authoring hub) and pure functions to merge logs, deduplicate by
identity, and report the per-hub high-water cursor a peer resumes from. It folds no
state and grants no claims — that is a separate responsibility (claims are mutual
exclusion, not a merge, and are never granted from a peer's log). It performs no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from synapse_channel.core.persistence import StoredEvent


@dataclass(frozen=True)
class HubEvent:
    """A stored event tagged with the id of the hub that authored it.

    Attributes
    ----------
    hub_id : str
        Id of the authoring hub; with ``seq`` it forms the event's global identity.
    seq : int
        The authoring hub's local monotonic sequence number.
    ts : float
        Event timestamp, the primary key of the deterministic replay order.
    kind : str
        Event kind (a :class:`synapse_channel.core.journal.EventKind` value).
    payload : Mapping[str, Any]
        The event body.
    """

    hub_id: str
    seq: int
    ts: float
    kind: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    @property
    def identity(self) -> tuple[str, int]:
        """Return the global identity ``(hub_id, seq)`` that dedupes the union."""
        return (self.hub_id, self.seq)

    @property
    def order_key(self) -> tuple[float, str, int]:
        """Return the deterministic total-order key ``(ts, hub_id, seq)``."""
        return (self.ts, self.hub_id, self.seq)

    @classmethod
    def from_stored(cls, hub_id: str, event: StoredEvent) -> HubEvent:
        """Tag a local :class:`StoredEvent` with its authoring hub id."""
        return cls(
            hub_id=str(hub_id),
            seq=int(event.seq),
            ts=float(event.ts),
            kind=str(event.kind),
            payload=event.payload,
        )


def tag_events(hub_id: str, events: Iterable[StoredEvent]) -> tuple[HubEvent, ...]:
    """Tag a hub's stored events with its id, ready to merge with a peer's log."""
    return tuple(HubEvent.from_stored(hub_id, event) for event in events)


def merge_event_logs(*logs: Iterable[HubEvent]) -> tuple[HubEvent, ...]:
    """Return the deterministic union of several hub-tagged logs.

    The union is a grow-only set keyed by ``(hub_id, seq)`` — duplicates (the same
    event seen from two peers) collapse to one — returned in the total order
    ``(ts, hub_id, seq)``. Because the key is the event's global identity and the
    order is total and content-derived, every peer that merges the same set of logs
    obtains the identical sequence, so a downstream fold is deterministic.

    On the rare ``(hub_id, seq)`` collision with *differing* content (a hub that
    reused a sequence — a misbehaving or rolled-back peer), the first occurrence in
    argument order wins and the conflicting duplicate is dropped, so the merge stays
    a function of its inputs rather than raising.
    """
    seen: dict[tuple[str, int], HubEvent] = {}
    for log in logs:
        for event in log:
            seen.setdefault(event.identity, event)
    return tuple(sorted(seen.values(), key=lambda event: event.order_key))


def hub_cursors(events: Iterable[HubEvent]) -> dict[str, int]:
    """Return the high-water ``seq`` per hub — the cursor a peer resumes sync from.

    A follower keeps, for each peer hub, the greatest ``seq`` it has folded; on the
    next sync it asks that peer only for events beyond the cursor. This is the
    ``(hub_id, seq)`` analogue of the single-hub ``read_since`` seam.
    """
    cursors: dict[str, int] = {}
    for event in events:
        if event.seq > cursors.get(event.hub_id, -1):
            cursors[event.hub_id] = event.seq
    return cursors
