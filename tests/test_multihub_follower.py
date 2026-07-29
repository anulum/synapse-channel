# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — multi-hub read-only follower regressions

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.clock_skew import ClockSkew
from synapse_channel.core.journal import EventKind
from synapse_channel.core.multihub_equivocation import (
    MultiHubEquivocationError,
    MultiHubPeerQuarantinedError,
    MultiHubSequenceError,
)
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


class _SkewAwarePeer(_FakePeer):
    """Fake peer exposing network metadata captured by the real fetcher."""

    def __init__(self, events: list[StoredEvent]) -> None:
        super().__init__(events)
        self.last_clock_skew = ClockSkew(peer_timestamp=90.0, observed_at=100.0, seconds=10.0)

    async def __call__(self, after_seq: int) -> Sequence[StoredEvent]:
        """Fetch events while exposing clock metadata on the callable object."""
        return await self.fetch(after_seq)


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
    assert state.board_provenance["T"].order_key == (1.0, "east", 1)
    assert state.observed_claims["T"].hub_id == "east"
    assert state.observed_claims["T"].claim["owner"] == "alpha"
    assert peer.cursors == [0]  # first poll starts from the beginning
    assert follower.cursor("east") == 2
    assert follower.peers() == ("east",)


async def test_poll_records_fetcher_clock_skew_metadata() -> None:
    peer = _SkewAwarePeer([_stored(1, 1.0, EventKind.LEDGER_TASK, task_id="T")])
    follower = MultiHubFollower()

    await follower.poll("east", peer)

    skew = follower.clock_skew("east")
    assert skew is not None
    assert skew.seconds == 10.0


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


async def test_poll_merges_multiple_peers_into_non_causal_display_order() -> None:
    west = _FakePeer([_stored(1, 5.0, EventKind.LEDGER_TASK, task_id="T", title="late")])
    east = _FakePeer([_stored(1, 1.0, EventKind.LEDGER_TASK, task_id="T", title="early")])
    follower = MultiHubFollower()
    await follower.poll("east", east.fetch)
    state = await follower.poll("west", west.fetch)
    # the later-timestamped declaration wins across the merged union
    assert state.board["T"]["title"] == "late"
    assert state.board_provenance["T"].hub_id == "west"
    assert state.to_dict()["board_policy"]["authoritative"] is False
    assert state.to_dict()["board_policy"]["causal"] is False
    assert follower.peers() == ("east", "west")


async def test_partition_assertions_do_not_cross_clear_equal_task_ids() -> None:
    east = _FakePeer([_stored(1, 1.0, EventKind.CLAIM, task_id="T", owner="OWNED/alice")])
    west = _FakePeer(
        [
            _stored(1, 2.0, EventKind.CLAIM, task_id="T", owner="OWNED/bob"),
            _stored(2, 3.0, EventKind.RELEASE, task_id="T"),
        ]
    )
    follower = MultiHubFollower()

    await follower.poll("east", east.fetch)
    await follower.poll("west", west.fetch)

    assert follower.asserting_owners(project_of=lambda owner: owner.split("/", 1)[0]) == {
        "OWNED": frozenset({"east"})
    }


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


async def test_conflicting_batch_is_rejected_before_state_or_cursor_publish(tmp_path: Path) -> None:
    journal = EventStore(tmp_path / "local.db")
    first = _stored(1, 1.0, EventKind.LEDGER_TASK, task_id="T", title="first")
    conflict = _stored(1, 1.0, EventKind.LEDGER_TASK, task_id="T", title="second")
    follower = MultiHubFollower(journal=journal, observer_id="local", clock=lambda: 50.0)

    with pytest.raises(MultiHubEquivocationError):
        await follower.poll("east", _FakePeer([first, conflict]).fetch)

    assert follower.cursor("east") == 0
    assert follower.observed().board == {}
    assert [item.peer_id for item in follower.quarantines()] == ["east"]
    evidence = journal.read_all()
    journal.close()
    assert [event.kind for event in evidence] == [EventKind.MULTIHUB_EQUIVOCATION]
    assert evidence[0].payload["seq"] == 1
    assert "title" not in evidence[0].payload


async def test_later_conflict_freezes_prior_view_cursor_and_higher_batch_events(
    tmp_path: Path,
) -> None:
    journal = EventStore(tmp_path / "local.db")
    follower = MultiHubFollower(journal=journal, clock=lambda: 60.0)
    accepted = _stored(1, 1.0, EventKind.LEDGER_TASK, task_id="T", title="accepted")
    await follower.poll("east", _FakePeer([accepted]).fetch)
    before = follower.observed().to_dict()
    conflict = _stored(1, 2.0, EventKind.LEDGER_TASK, task_id="T", title="conflict")
    later = _stored(2, 3.0, EventKind.LEDGER_TASK, task_id="U", title="must-not-publish")

    async def hostile_replay(_after_seq: int) -> Sequence[StoredEvent]:
        return [conflict, later]

    with pytest.raises(MultiHubEquivocationError):
        await follower.poll("east", hostile_replay)

    assert follower.cursor("east") == 1
    assert follower.observed().to_dict() == before
    assert "U" not in follower.observed().board
    journal.close()


async def test_quarantine_survives_restart_and_blocks_fetch_until_explicit_recovery(
    tmp_path: Path,
) -> None:
    path = tmp_path / "local.db"
    journal = EventStore(path)
    follower = MultiHubFollower(journal=journal, clock=lambda: 70.0)
    first = _stored(1, 1.0, EventKind.LEDGER_TASK, task_id="T", title="one")
    conflict = _stored(1, 2.0, EventKind.LEDGER_TASK, task_id="T", title="two")
    with pytest.raises(MultiHubEquivocationError):
        await follower.poll("east", _FakePeer([first, conflict]).fetch)
    journal.close()

    reopened = EventStore(path)
    restarted = MultiHubFollower(journal=reopened, clock=lambda: 80.0)
    blocked = _FakePeer([first])
    with pytest.raises(MultiHubPeerQuarantinedError):
        await restarted.poll("east", blocked.fetch)
    assert blocked.cursors == []

    restarted.recover_peer(
        "east",
        recovered_by="operator",
        reason="accepted signed generation reset",
        new_log_generation="east-generation-2",
    )
    state = await restarted.poll("east", blocked.fetch)
    assert state.board["T"]["title"] == "one"
    assert [event.kind for event in reopened.read_all()] == [
        EventKind.MULTIHUB_EQUIVOCATION,
        EventKind.MULTIHUB_EQUIVOCATION_RECOVERY,
    ]
    reopened.close()


async def test_same_peer_polls_are_serialized_before_the_second_fetch() -> None:
    first_started = asyncio.Event()
    allow_first = asyncio.Event()
    cursors: list[int] = []

    async def fetch(after_seq: int) -> Sequence[StoredEvent]:
        cursors.append(after_seq)
        if len(cursors) == 1:
            first_started.set()
            await allow_first.wait()
            return [_stored(1, 1.0, EventKind.LEDGER_TASK, task_id="T")]
        return []

    follower = MultiHubFollower()
    first_poll = asyncio.create_task(follower.poll("east", fetch))
    await first_started.wait()
    second_poll = asyncio.create_task(follower.poll("east", fetch))
    await asyncio.sleep(0)
    assert cursors == [0]
    allow_first.set()
    await asyncio.gather(first_poll, second_poll)
    assert cursors == [0, 1]


def test_recovery_rejects_unknown_peer_and_incomplete_ceremony() -> None:
    follower = MultiHubFollower()
    with pytest.raises(ValueError, match="not quarantined"):
        follower.recover_peer(
            "east", recovered_by="operator", reason="reason", new_log_generation="generation"
        )


@pytest.mark.parametrize(
    ("recovered_by", "reason", "generation", "missing"),
    [
        ("", "reason", "generation", "recovered_by"),
        ("operator", "", "generation", "reason"),
        ("operator", "reason", "", "new_log_generation"),
    ],
)
async def test_recovery_rejects_an_incomplete_ceremony(
    recovered_by: str, reason: str, generation: str, missing: str
) -> None:
    follower = MultiHubFollower()
    first = _stored(1, 1.0, EventKind.LEDGER_TASK, task_id="T", title="one")
    conflict = _stored(1, 2.0, EventKind.LEDGER_TASK, task_id="T", title="two")
    with pytest.raises(MultiHubEquivocationError):
        await follower.poll("east", _FakePeer([first, conflict]).fetch)

    with pytest.raises(ValueError, match=missing):
        follower.recover_peer(
            "east",
            recovered_by=recovered_by,
            reason=reason,
            new_log_generation=generation,
        )
    assert [quarantine.peer_id for quarantine in follower.quarantines()] == ["east"]


@pytest.mark.parametrize(
    "events",
    [
        [_stored(2, 2.0, EventKind.LEDGER_TASK, task_id="gap")],
        [
            _stored(1, 1.0, EventKind.LEDGER_TASK, task_id="one"),
            _stored(3, 3.0, EventKind.LEDGER_TASK, task_id="descending-gap"),
            _stored(2, 2.0, EventKind.LEDGER_TASK, task_id="two"),
        ],
    ],
)
async def test_sequence_protocol_violations_leave_state_and_cursor_unchanged(
    events: list[StoredEvent],
) -> None:
    follower = MultiHubFollower()

    with pytest.raises(MultiHubSequenceError):
        await follower.poll("east", _FakePeer(events).fetch)

    assert follower.cursor("east") == 0
    assert follower.observed().board == {}


async def test_exact_duplicate_across_cursor_is_idempotent() -> None:
    event = _stored(1, 1.0, EventKind.LEDGER_TASK, task_id="T")
    follower = MultiHubFollower()
    await follower.poll("east", _FakePeer([event]).fetch)

    async def replay(_after_seq: int) -> Sequence[StoredEvent]:
        return [event]

    state = await follower.poll("east", replay)
    assert follower.cursor("east") == 1
    assert state.board["T"]["task_id"] == "T"


@pytest.mark.parametrize(
    "hostile_batch",
    [
        [
            _stored(1, 9.0, EventKind.LEDGER_TASK, task_id="T", title="conflict"),
            _stored(2, 2.0, EventKind.LEDGER_TASK, task_id="U"),
            _stored(3, 3.0, EventKind.LEDGER_TASK, task_id="V"),
        ],
        [
            _stored(1, 1.0, EventKind.LEDGER_TASK, task_id="T", title="accepted"),
            _stored(1, 9.0, EventKind.LEDGER_TASK, task_id="T", title="conflict"),
            _stored(2, 2.0, EventKind.LEDGER_TASK, task_id="U"),
        ],
        [
            _stored(1, 1.0, EventKind.LEDGER_TASK, task_id="T", title="accepted"),
            _stored(2, 2.0, EventKind.LEDGER_TASK, task_id="U"),
            _stored(1, 9.0, EventKind.LEDGER_TASK, task_id="T", title="conflict"),
        ],
    ],
)
async def test_conflict_position_never_partially_publishes_batch(
    hostile_batch: list[StoredEvent], tmp_path: Path
) -> None:
    journal = EventStore(tmp_path / "local.db")
    accepted = _stored(1, 1.0, EventKind.LEDGER_TASK, task_id="T", title="accepted")
    follower = MultiHubFollower(journal=journal, clock=lambda: 90.0)
    await follower.poll("east", _FakePeer([accepted]).fetch)
    before = follower.observed().to_dict()

    async def hostile(_after_seq: int) -> Sequence[StoredEvent]:
        return hostile_batch

    with pytest.raises(MultiHubEquivocationError):
        await follower.poll("east", hostile)

    assert follower.observed().to_dict() == before
    assert follower.cursor("east") == 1
    assert [event.kind for event in journal.read_all()] == [EventKind.MULTIHUB_EQUIVOCATION]
    journal.close()


async def test_concurrent_conflict_is_serialized_and_records_one_quarantine(
    tmp_path: Path,
) -> None:
    journal = EventStore(tmp_path / "local.db")
    follower = MultiHubFollower(journal=journal, clock=lambda: 91.0)
    first_started = asyncio.Event()
    allow_first = asyncio.Event()
    calls = 0

    async def fetch(_after_seq: int) -> Sequence[StoredEvent]:
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            await allow_first.wait()
            return [_stored(1, 1.0, EventKind.LEDGER_TASK, task_id="T", title="one")]
        return [_stored(1, 2.0, EventKind.LEDGER_TASK, task_id="T", title="two")]

    first = asyncio.create_task(follower.poll("east", fetch))
    await first_started.wait()
    second = asyncio.create_task(follower.poll("east", fetch))
    allow_first.set()
    await first
    with pytest.raises(MultiHubEquivocationError):
        await second

    with pytest.raises(MultiHubPeerQuarantinedError):
        await follower.poll("east", fetch)
    assert calls == 2
    assert [event.kind for event in journal.read_all()] == [EventKind.MULTIHUB_EQUIVOCATION]
    journal.close()


async def test_evidence_write_failure_preserves_typed_conflict_and_in_memory_quarantine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = EventStore(tmp_path / "local.db")
    follower = MultiHubFollower(journal=journal)
    first = _stored(1, 1.0, EventKind.LEDGER_TASK, task_id="T", title="one")
    conflict = _stored(1, 2.0, EventKind.LEDGER_TASK, task_id="T", title="two")

    def fail_evidence(*_args: object, **_kwargs: object) -> int:
        raise OSError("simulated durable evidence failure")

    monkeypatch.setattr(
        "synapse_channel.core.multihub_follower.record_multihub_equivocation",
        fail_evidence,
    )
    with pytest.raises(MultiHubEquivocationError) as raised:
        await follower.poll("east", _FakePeer([first, conflict]).fetch)

    assert isinstance(raised.value.__cause__, OSError)
    assert follower.cursor("east") == 0
    with pytest.raises(MultiHubPeerQuarantinedError):
        await follower.poll("east", _FakePeer([]).fetch)
    journal.close()


async def test_fold_failure_does_not_publish_candidate_or_fetch_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    peer = _SkewAwarePeer([_stored(1, 1.0, EventKind.LEDGER_TASK, task_id="T")])
    follower = MultiHubFollower()

    def fail_fold(_events: object) -> object:
        raise ValueError("simulated semantic fold failure")

    monkeypatch.setattr("synapse_channel.core.multihub_follower.fold_observed_state", fail_fold)
    with pytest.raises(ValueError, match="semantic fold failure"):
        await follower.poll("east", peer)

    assert follower.cursor("east") == 0
    assert follower.clock_skew("east") is None
    monkeypatch.undo()
    assert follower.observed().board == {}
