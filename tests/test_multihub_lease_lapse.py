# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a claim whose lease lapsed on its own hub stops asserting authority
"""A claim whose lease lapsed on the hub that granted it no longer asserts ownership.

A hub expires a lapsed claim in memory and journals no release. A follower that keeps such a
claim live until a release would see the granting hub assert its namespace for ever, and a
later grant by another hub would look like a partition that never heals. A claim is lapsed
once the granting hub's own journal has moved to or past its ``lease_expires_at``: the
comparison uses only that hub's clock, never the follower's or another hub's.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.journal import EventKind
from synapse_channel.core.multihub_fold import asserting_owners_from_events, fold_observed_state
from synapse_channel.core.multihub_follower import EventFetcher
from synapse_channel.core.multihub_merge import HubEvent, merge_event_logs
from synapse_channel.core.multihub_watch import MultiHubWatch
from synapse_channel.core.namespace_ownership import NamespaceOwnership
from synapse_channel.core.persistence import EventStore, StoredEvent


def _ev(hub: str, seq: int, ts: float, kind: str, **payload: Any) -> HubEvent:
    """Return one hub-tagged event."""
    return HubEvent(hub_id=hub, seq=seq, ts=ts, kind=kind, payload=payload)


def _project_of(agent: str) -> str:
    """Return the namespace the ownership gate derives: the prefix before the first slash."""
    return agent.split("/", 1)[0] if "/" in agent else ""


def _claim(hub: str, seq: int, ts: float, task: str, lease: float | None) -> HubEvent:
    """Return a claim granted by ``hub`` in namespace OWNED, with its lease when given."""
    payload: dict[str, Any] = {"task_id": task, "owner": "OWNED/alice"}
    if lease is not None:
        payload["lease_expires_at"] = lease
    return _ev(hub, seq, ts, EventKind.CLAIM, **payload)


def test_a_claim_lapsed_on_its_own_hub_no_longer_asserts() -> None:
    """The granting hub's journal passed the lease, so the claim no longer asserts OWNED."""
    events = [
        _claim("hub-a", 1, 1.0, "T", lease=10.0),
        _ev("hub-a", 2, 11.0, EventKind.CHAT, sender="hub-a/agent", payload="later"),
    ]
    assert asserting_owners_from_events(events, project_of=_project_of) == {}


def test_a_claim_lapses_when_its_hub_reaches_the_lease_exactly() -> None:
    """A lease that has reached its expiry has lapsed, as the granting hub expires it."""
    events = [
        _claim("hub-a", 1, 1.0, "T", lease=10.0),
        _ev("hub-a", 2, 10.0, EventKind.CHAT, sender="hub-a/agent", payload="at the lease"),
    ]
    assert asserting_owners_from_events(events, project_of=_project_of) == {}


def test_a_hub_clock_stepping_back_does_not_revive_a_lapsed_claim() -> None:
    """A hub clock that steps back after passing the lease does not revive the claim."""
    events = [
        _claim("hub-a", 1, 1.0, "T", lease=100.0),
        _ev("hub-a", 2, 120.0, EventKind.CHAT, sender="hub-a/agent", payload="past the lease"),
        _ev("hub-a", 3, 90.0, EventKind.CHAT, sender="hub-a/agent", payload="after a clock step"),
    ]
    assert asserting_owners_from_events(events, project_of=_project_of) == {}
    observed = fold_observed_state(merge_event_logs(events))
    assert observed.observed_claims == {}


def test_a_claim_within_its_lease_still_asserts() -> None:
    """Before the granting hub reaches the lease, the claim still asserts its namespace."""
    events = [
        _claim("hub-a", 1, 1.0, "T", lease=10.0),
        _ev("hub-a", 2, 9.0, EventKind.CHAT, sender="hub-a/agent", payload="earlier"),
    ]
    assert asserting_owners_from_events(events, project_of=_project_of) == {
        "OWNED": frozenset({"hub-a"})
    }


def test_another_hubs_clock_never_lapses_a_claim() -> None:
    """Only the granting hub's own journal can lapse its claim."""
    events = [
        _claim("hub-a", 1, 1.0, "T", lease=10.0),
        _ev("hub-b", 1, 100.0, EventKind.CHAT, sender="hub-b/agent", payload="late"),
    ]
    assert asserting_owners_from_events(events, project_of=_project_of) == {
        "OWNED": frozenset({"hub-a"})
    }


def test_a_new_claim_after_the_lapse_asserts_again() -> None:
    """A fresh grant after the lapse carries its own lease and asserts again."""
    events = [
        _claim("hub-a", 1, 1.0, "T", lease=10.0),
        _ev("hub-a", 2, 11.0, EventKind.CHAT, sender="hub-a/agent", payload="lapsed"),
        _claim("hub-a", 3, 12.0, "T", lease=30.0),
    ]
    assert asserting_owners_from_events(events, project_of=_project_of) == {
        "OWNED": frozenset({"hub-a"})
    }


def test_a_task_update_without_a_lease_keeps_the_claims_lease() -> None:
    """An update that carries no lease neither extends nor erases the granted one."""
    events = [
        _claim("hub-a", 1, 1.0, "T", lease=10.0),
        _ev("hub-a", 2, 5.0, EventKind.TASK_UPDATE, task_id="T", owner="OWNED/alice"),
        _ev("hub-a", 3, 11.0, EventKind.CHAT, sender="hub-a/agent", payload="lapsed"),
    ]
    assert asserting_owners_from_events(events, project_of=_project_of) == {}


def test_a_claim_without_a_lease_is_never_lapsed() -> None:
    """A claim whose lease is unknown stays live until its release, as before."""
    events = [
        _claim("hub-a", 1, 1.0, "T", lease=None),
        _ev("hub-a", 2, 1_000.0, EventKind.CHAT, sender="hub-a/agent", payload="much later"),
    ]
    assert asserting_owners_from_events(events, project_of=_project_of) == {
        "OWNED": frozenset({"hub-a"})
    }


def test_a_regrant_without_a_lease_forgets_the_old_one() -> None:
    """A new grant that states no lease is not lapsed by the lease of the grant it replaced."""
    events = [
        _claim("hub-a", 1, 1.0, "T", lease=10.0),
        _claim("hub-a", 2, 5.0, "T", lease=None),
        _ev("hub-a", 3, 11.0, EventKind.CHAT, sender="hub-a/agent", payload="later"),
    ]
    assert asserting_owners_from_events(events, project_of=_project_of) == {
        "OWNED": frozenset({"hub-a"})
    }


@pytest.mark.parametrize("lease", [True, "10", float("inf"), float("nan")])
def test_a_lease_that_is_not_a_finite_number_is_unknown(lease: object) -> None:
    """A lease field that is no finite number states no lease, so the claim stays live."""
    events = [
        _ev(
            "hub-a",
            1,
            1.0,
            EventKind.CLAIM,
            task_id="T",
            owner="OWNED/alice",
            lease_expires_at=lease,
        ),
        _ev("hub-a", 2, 1_000.0, EventKind.CHAT, sender="hub-a/agent", payload="much later"),
    ]
    assert asserting_owners_from_events(events, project_of=_project_of) == {
        "OWNED": frozenset({"hub-a"})
    }


def test_a_release_forgets_the_lease_of_the_claim_it_ends() -> None:
    """After a release, a later lease-less grant of the same task is not lapsed by the old lease."""
    events = [
        _claim("hub-a", 1, 1.0, "T", lease=10.0),
        _ev("hub-a", 2, 2.0, EventKind.RELEASE, task_id="T"),
        _ev("hub-a", 3, 3.0, EventKind.TASK_UPDATE, task_id="T", owner="OWNED/alice"),
        _ev("hub-a", 4, 11.0, EventKind.CHAT, sender="hub-a/agent", payload="later"),
    ]
    assert asserting_owners_from_events(events, project_of=_project_of) == {
        "OWNED": frozenset({"hub-a"})
    }


def test_the_observed_claim_view_drops_a_lapsed_claim() -> None:
    """The advisory claim view stops showing a claim its own hub has let lapse."""
    hub_a = [
        _claim("hub-a", 1, 1.0, "T", lease=10.0),
        _ev("hub-a", 2, 11.0, EventKind.CHAT, sender="hub-a/agent", payload="lapsed"),
    ]
    hub_b = [_claim("hub-b", 1, 2.0, "U", lease=50.0)]
    observed = fold_observed_state(merge_event_logs(hub_a, hub_b))
    assert sorted(observed.observed_claims) == ["U"]


def _stored(seq: int, ts: float, kind: str, payload: dict[str, Any]) -> StoredEvent:
    """Return one journalled event as a peer's log serves it."""
    return StoredEvent(seq=seq, ts=ts, kind=kind, payload=payload)


class _ScriptedPeer:
    """A per-peer fetcher that serves queued batches, then nothing more."""

    def __init__(self, batches: list[Sequence[StoredEvent]]) -> None:
        self._batches = list(batches)

    async def fetch(self, after_seq: int) -> Sequence[StoredEvent]:
        """Return the next queued batch, or nothing once they are served."""
        del after_seq
        return self._batches.pop(0) if self._batches else []


async def test_a_watch_heals_a_partition_once_the_peers_lease_lapses(tmp_path: Path) -> None:
    """A peer's lapsed claim no longer contests OWNED, and the watch journals the heal."""
    store = EventStore(tmp_path / "hub.db")
    peer = _ScriptedPeer(
        [
            [
                _stored(
                    1,
                    1.0,
                    EventKind.CLAIM,
                    {"task_id": "T1", "owner": "OWNED/alice", "lease_expires_at": 10.0},
                )
            ],
            [_stored(2, 11.0, EventKind.CHAT, {"sender": "hub-b/agent", "payload": "later"})],
        ]
    )

    def factory(uri: str, *, local_id: str, token: str | None = None) -> EventFetcher:
        del uri, local_id, token
        return peer.fetch

    watch = MultiHubWatch(
        {"hub-b": "hub-b"},
        local_id="watch-test",
        interval=1.0,
        namespace_of=_project_of,
        fetcher_factory=factory,
        namespace_ownership=NamespaceOwnership(owners={"OWNED": "syn-a"}, local_hub_id="syn-a"),
        journal=store,
    )

    await watch.poll_once()
    assert watch.observed_asserting_hubs("OWNED") == ("hub-b",)
    await watch.poll_once()

    assert watch.observed_asserting_hubs("OWNED") == ()
    assert [event.kind for event in store.read_all()] == [
        EventKind.MULTIHUB_PARTITION,
        EventKind.MULTIHUB_HEAL,
    ]
