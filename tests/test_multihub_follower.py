# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — multi-hub read-only follower regressions

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from synapse_channel.core.journal import EventKind
from synapse_channel.core.multihub_follower import MultiHubFollower, store_fetcher
from synapse_channel.core.persistence import EventStore, StoredEvent


def _stored(seq: int, ts: float, kind: str, **payload: Any) -> StoredEvent:
    return StoredEvent(seq=seq, ts=ts, kind=kind, payload=payload)


class _FakePeer:
    """A peer log behind a ``read_since``-style fetcher that records its cursors."""

    def __init__(self, events: list[StoredEvent]) -> None:
        self.events = events
        self.cursors: list[int] = []

    async def fetch(self, after_seq: int) -> Sequence[StoredEvent]:
        self.cursors.append(after_seq)
        return [event for event in self.events if event.seq > after_seq]


async def test_poll_folds_a_peer_log_into_the_observed_view() -> None:
    peer = _FakePeer(
        [
            _stored(1, 1.0, EventKind.LEDGER_TASK, task_id="T", title="build", status="open"),
            _stored(2, 2.0, EventKind.CLAIM, task_id="T", owner="alpha"),
        ]
    )
    follower = MultiHubFollower()
    state = await follower.poll("east", peer.fetch)

    assert state.board["T"]["title"] == "build"
    assert state.observed_claims["T"].hub_id == "east"
    assert state.observed_claims["T"].claim["owner"] == "alpha"
    assert peer.cursors == [0]  # first poll starts from the beginning
    assert follower.cursor("east") == 2
    assert follower.peers() == ("east",)


async def test_poll_is_incremental_and_idempotent() -> None:
    peer = _FakePeer([_stored(1, 1.0, EventKind.LEDGER_TASK, task_id="T", title="one")])
    follower = MultiHubFollower()
    await follower.poll("east", peer.fetch)

    # a new event appears; the second poll resumes from the advanced cursor
    peer.events.append(_stored(2, 2.0, EventKind.LEDGER_TASK, task_id="T", title="two"))
    state = await follower.poll("east", peer.fetch)
    assert peer.cursors == [0, 1]
    assert state.board["T"]["title"] == "two"

    # polling again with nothing new leaves the view and cursor unchanged
    same = await follower.poll("east", peer.fetch)
    assert peer.cursors == [0, 1, 2]
    assert same.board["T"]["title"] == "two"
    assert follower.cursor("east") == 2


async def test_poll_merges_multiple_peers_by_timestamp() -> None:
    west = _FakePeer([_stored(1, 5.0, EventKind.LEDGER_TASK, task_id="T", title="late")])
    east = _FakePeer([_stored(1, 1.0, EventKind.LEDGER_TASK, task_id="T", title="early")])
    follower = MultiHubFollower()
    await follower.poll("east", east.fetch)
    state = await follower.poll("west", west.fetch)
    # the later-timestamped declaration wins across the merged union
    assert state.board["T"]["title"] == "late"
    assert follower.peers() == ("east", "west")


async def test_poll_on_an_empty_peer_keeps_an_empty_view() -> None:
    follower = MultiHubFollower()
    state = await follower.poll("east", _FakePeer([]).fetch)
    assert state.board == {}
    assert state.observed_claims == {}
    # an unseen peer reports a zero cursor
    assert follower.cursor("ghost") == 0


async def test_store_fetcher_reads_a_real_event_store_over_read_since() -> None:
    store = EventStore(":memory:")
    try:
        store.append(EventKind.LEDGER_TASK, {"task_id": "T", "title": "real"}, ts=1.0)
        store.append(EventKind.CLAIM, {"task_id": "T", "owner": "alpha"}, ts=2.0)
        follower = MultiHubFollower()
        state = await follower.poll("east", store_fetcher(store))
    finally:
        store.close()

    assert state.board["T"]["title"] == "real"
    assert state.observed_claims["T"].claim["owner"] == "alpha"
    assert follower.cursor("east") == 2
