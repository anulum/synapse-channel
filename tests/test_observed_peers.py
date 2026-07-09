# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — observed peer operator-surface tests

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

import pytest

from synapse_channel.core.journal import EventKind
from synapse_channel.core.multihub_fold import fold_observed_state
from synapse_channel.core.multihub_merge import HubEvent
from synapse_channel.core.persistence import StoredEvent
from synapse_channel.observed_peers import (
    ObservedFetcher,
    ObservedPeerSnapshot,
    ObservedPeerSpec,
    fetch_observed_peer,
    fetch_observed_peers,
    network_observed_fetcher_factory,
    observed_claim_count,
    observed_max_lag,
    observed_peers_to_dict,
    parse_observed_peer,
    parse_observed_peers,
)


def _event(seq: int, kind: str, **payload: object) -> StoredEvent:
    """Build a stored event for observed-peer tests."""
    return StoredEvent(seq=seq, ts=float(seq), kind=kind, payload=payload)


class _Fetcher:
    """Fake observed fetcher exposing the high-water metadata real transport keeps."""

    def __init__(self, events: Sequence[StoredEvent], *, log_end_seq: int | None = None) -> None:
        self.events = tuple(events)
        self.last_log_end_seq = log_end_seq
        self.cursors: list[int] = []

    async def __call__(self, after_seq: int) -> Sequence[StoredEvent]:
        self.cursors.append(after_seq)
        return [event for event in self.events if event.seq > after_seq]


def test_parse_observed_peer_requires_hub_and_uri() -> None:
    assert parse_observed_peer("east=ws://127.0.0.1:8877") == ObservedPeerSpec(
        hub_id="east", uri="ws://127.0.0.1:8877"
    )
    assert parse_observed_peers(["west=ws://127.0.0.1:8878"]) == (
        ObservedPeerSpec(hub_id="west", uri="ws://127.0.0.1:8878"),
    )
    assert parse_observed_peers(None) == ()
    with pytest.raises(ValueError, match="HUB=URI"):
        parse_observed_peer("east")


def test_network_fetcher_factory_passes_transport_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str | None, float]] = []
    fetcher = _Fetcher([])

    def fake_network_fetcher(
        uri: str,
        *,
        local_id: str,
        token: str | None,
        timeout: float,
    ) -> _Fetcher:
        calls.append((uri, local_id, token, timeout))
        return fetcher

    monkeypatch.setattr("synapse_channel.observed_peers.network_fetcher", fake_network_fetcher)

    build = network_observed_fetcher_factory(local_id="local", token="secret", timeout=3.0)

    assert build(ObservedPeerSpec("east", "ws://east")) is fetcher
    assert calls == [("ws://east", "local", "secret", 3.0)]


async def test_fetch_observed_peer_folds_claims_and_lag() -> None:
    fetcher = _Fetcher(
        [
            _event(1, EventKind.LEDGER_TASK, task_id="T", title="remote"),
            _event(2, EventKind.CLAIM, task_id="T", owner="REMOTE/agent", paths=["src/x.py"]),
        ],
        log_end_seq=5,
    )
    spec = ObservedPeerSpec("east", "ws://east")
    snapshot = await fetch_observed_peer(spec, fetcher_factory=lambda _spec: fetcher)

    assert snapshot.reachable is True
    assert snapshot.cursor == 2
    assert snapshot.log_end_seq == 5
    assert snapshot.lag == 3
    assert snapshot.observed_agents == ("REMOTE/agent",)
    assert snapshot.state.observed_claims["T"].hub_id == "east"
    assert fetcher.cursors == [0]


async def test_fetch_observed_peers_preserves_unreachable_peer_rows() -> None:
    class _FailingFetcher:
        def __init__(self) -> None:
            self.last_log_end_seq: int | None = None

        async def __call__(self, _after_seq: int) -> Sequence[StoredEvent]:
            raise OSError("offline")

    def build(_spec: ObservedPeerSpec) -> ObservedFetcher:
        return cast(ObservedFetcher, _FailingFetcher())

    snapshots = await fetch_observed_peers(
        [ObservedPeerSpec("down", "ws://down")],
        fetcher_factory=build,
    )

    assert snapshots == (
        ObservedPeerSnapshot(hub_id="down", uri="ws://down", reachable=False, error="offline"),
    )
    assert await fetch_observed_peers([], fetcher_factory=build) == ()


def test_observed_peer_summary_helpers() -> None:
    snapshot = ObservedPeerSnapshot(
        hub_id="east",
        uri="ws://east",
        reachable=True,
        cursor=2,
        log_end_seq=4,
        state=fold_observed_state(
            [HubEvent("east", 2, 2.0, EventKind.CLAIM, {"task_id": "T", "owner": "a"})]
        ),
    )
    assert observed_claim_count((snapshot,)) == 1
    assert observed_max_lag((snapshot,)) == 2
    assert observed_peers_to_dict((snapshot,))[0]["hub_id"] == "east"
    assert ObservedPeerSnapshot("empty", "ws://empty", True).lag is None
    assert observed_claim_count((ObservedPeerSnapshot("down", "ws://down", False),)) == 0
    assert observed_max_lag(()) is None
