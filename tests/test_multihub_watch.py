# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — the hub's standing follower feeding partition detection

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

import pytest

from synapse_channel.core.journal import EventKind
from synapse_channel.core.multihub_follower import EventFetcher
from synapse_channel.core.multihub_transport import MultiHubFetchError
from synapse_channel.core.multihub_watch import (
    DEFAULT_WATCH_INTERVAL,
    MIN_WATCH_INTERVAL,
    MultiHubWatch,
    parse_watch_peers,
    parse_watch_pins,
)
from synapse_channel.core.persistence import StoredEvent


def _claim(seq: int, owner: str) -> StoredEvent:
    """One journalled claim event, as a peer's log would serve it."""
    return StoredEvent(
        seq=seq, ts=float(seq), kind=EventKind.CLAIM, payload={"task_id": f"T{seq}", "owner": owner}
    )


class _ScriptedPeer:
    """A per-peer fetcher: serves queued batches, then raises what it is told to."""

    def __init__(self, batches: list[Sequence[StoredEvent]], *, then_fail: bool = False) -> None:
        self._batches = list(batches)
        self._then_fail = then_fail
        self.calls: list[int] = []

    async def fetch(self, after_seq: int) -> Sequence[StoredEvent]:
        self.calls.append(after_seq)
        if self._batches:
            return self._batches.pop(0)
        if self._then_fail:
            raise MultiHubFetchError("peer unreachable")
        return []


def _watch(
    peers: dict[str, _ScriptedPeer], *, interval: float = DEFAULT_WATCH_INTERVAL
) -> MultiHubWatch:
    """Build a watch whose transport is the scripted peers instead of sockets."""
    captured: dict[str, _ScriptedPeer] = dict(peers)

    def factory(uri: str, *, local_id: str, token: str | None = None) -> EventFetcher:
        del local_id, token
        return captured[uri].fetch

    return MultiHubWatch(
        {peer: peer for peer in peers},  # uri == peer id, resolved by the factory
        local_id="watch-test",
        interval=interval,
        namespace_of=lambda agent: agent.split("/", 1)[0] if "/" in agent else "",
        fetcher_factory=factory,
    )


class TestParseWatchPeers:
    def test_parses_peers_in_order(self) -> None:
        parsed = parse_watch_peers(["hub-b=ws://b:8876", " hub-c = wss://c:443 "])
        assert parsed == {"hub-b": "ws://b:8876", "hub-c": "wss://c:443"}

    @pytest.mark.parametrize("value", ["no-separator", "=ws://b", "hub-b=", "  =  "])
    def test_rejects_a_malformed_value(self, value: str) -> None:
        with pytest.raises(ValueError, match="PEER=URI"):
            parse_watch_peers([value])

    def test_rejects_a_repeated_peer(self) -> None:
        with pytest.raises(ValueError, match="names peer 'hub-b' twice"):
            parse_watch_peers(["hub-b=ws://one", "hub-b=ws://two"])


class TestWatchPolling:
    async def test_a_successful_poll_populates_the_assertions(self) -> None:
        watch = _watch({"hub-b": _ScriptedPeer([[_claim(1, "OWNED/alice")]])})
        assert watch.observed_asserting_hubs("OWNED") == ()
        outcomes = await watch.poll_once()
        assert outcomes == {"hub-b": None}
        assert watch.observed_asserting_hubs("OWNED") == ("hub-b",)
        assert watch.observed_asserting_hubs("OTHER") == ()

    async def test_assertions_union_across_peers_sorted(self) -> None:
        watch = _watch(
            {
                "hub-c": _ScriptedPeer([[_claim(2, "OWNED/bob")]]),
                "hub-b": _ScriptedPeer([[_claim(1, "OWNED/alice")]]),
            }
        )
        await watch.poll_once()
        assert watch.observed_asserting_hubs("OWNED") == ("hub-b", "hub-c")

    async def test_a_failed_peer_neither_blocks_nor_clears_the_others(self) -> None:
        ok = _ScriptedPeer([[_claim(1, "OWNED/alice")]])
        down = _ScriptedPeer([], then_fail=True)
        watch = _watch({"hub-down": down, "hub-b": ok})
        outcomes = await watch.poll_once()
        assert outcomes["hub-b"] is None
        assert "peer unreachable" in str(outcomes["hub-down"])
        assert watch.observed_asserting_hubs("OWNED") == ("hub-b",)

    async def test_a_fully_failed_round_keeps_the_stale_observation(self) -> None:
        peer = _ScriptedPeer([[_claim(1, "OWNED/alice")]], then_fail=True)
        watch = _watch({"hub-b": peer})
        await watch.poll_once()
        assert watch.observed_asserting_hubs("OWNED") == ("hub-b",)
        outcomes = await watch.poll_once()  # the peer now raises
        assert outcomes["hub-b"] is not None
        assert watch.observed_asserting_hubs("OWNED") == ("hub-b",)  # retained suspicion

    async def test_polls_resume_from_the_peer_cursor(self) -> None:
        peer = _ScriptedPeer([[_claim(1, "OWNED/alice")], [_claim(2, "OWNED/bob")]])
        watch = _watch({"hub-b": peer})
        await watch.poll_once()
        await watch.poll_once()
        assert peer.calls == [0, 1]
        assert watch.observed_asserting_hubs("OWNED") == ("hub-b",)

    async def test_the_default_namespace_derivation_matches_the_gate(self) -> None:
        peer = _ScriptedPeer([[_claim(1, "SYNAPSE-CHANNEL/claude-e57b")]])

        def factory(uri: str, *, local_id: str, token: str | None = None) -> EventFetcher:
            del uri, local_id, token
            return peer.fetch

        watch = MultiHubWatch(
            {"hub-b": "ws://b:8876"}, local_id="watch-test", fetcher_factory=factory
        )
        await watch.poll_once()
        assert watch.observed_asserting_hubs("SYNAPSE-CHANNEL") == ("hub-b",)


class TestWatchConstruction:
    def test_the_interval_is_clamped_to_the_minimum(self) -> None:
        watch = _watch({}, interval=0.01)
        assert watch.interval == MIN_WATCH_INTERVAL

    def test_the_default_interval_is_kept(self) -> None:
        watch = _watch({})
        assert watch.interval == DEFAULT_WATCH_INTERVAL

    def test_the_factory_receives_uri_local_id_and_token(self) -> None:
        captured: dict[str, object] = {}

        def factory(uri: str, *, local_id: str, token: str | None = None) -> EventFetcher:
            captured.update({"uri": uri, "local_id": local_id, "token": token})

            async def fetch(after_seq: int) -> Sequence[StoredEvent]:
                del after_seq
                return []  # pragma: no cover - construction-only test

            return fetch

        MultiHubWatch(
            {"hub-b": "wss://b:443"}, local_id="syn-a", token="secret", fetcher_factory=factory
        )
        assert captured == {"uri": "wss://b:443", "local_id": "syn-a", "token": "secret"}


class TestWatchRun:
    async def test_run_polls_each_round_and_sleeps_between_them(self) -> None:
        peer = _ScriptedPeer([[_claim(1, "OWNED/alice")], [], []])
        watch = _watch({"hub-b": peer}, interval=5.0)
        naps: list[float] = []

        async def sleeper(seconds: float) -> None:
            naps.append(seconds)

        await watch.run(sleeper=sleeper, rounds=3)
        assert peer.calls == [0, 1, 1]
        assert naps == [5.0, 5.0]  # rounds - 1 sleeps; no trailing nap after the last round

    async def test_a_single_round_never_sleeps(self) -> None:
        watch = _watch({"hub-b": _ScriptedPeer([[]])})

        async def sleeper(seconds: float) -> None:  # pragma: no cover - must not run
            raise AssertionError(f"unexpected sleep of {seconds}s")

        await watch.run(sleeper=sleeper, rounds=1)

    async def test_run_surfaces_an_unexpected_watch_death_at_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An exception poll_once does not absorb (not a MultiHubFetchError) stops the loop
        but is logged at WARNING first, so the watch never dies silently."""

        async def exploding_fetch(after_seq: int) -> Sequence[StoredEvent]:
            del after_seq
            raise RuntimeError("watch loop bug")

        def factory(uri: str, *, local_id: str, token: str | None = None) -> EventFetcher:
            del uri, local_id, token
            return exploding_fetch

        watch = MultiHubWatch({"hub-b": "ws://b"}, local_id="w", fetcher_factory=factory)
        with caplog.at_level(logging.WARNING, logger="synapse_channel.core.multihub_watch"):
            with pytest.raises(RuntimeError, match="watch loop bug"):
                await watch.run(rounds=1)
        assert "multihub watch stopped on an unexpected error" in caplog.text
        assert "watch loop bug" in caplog.text

    async def test_cancellation_stops_the_loop_without_an_error_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Cancellation is the standing task's ordinary shutdown: it propagates untouched and
        is never logged as a watch death."""
        watch = _watch({"hub-b": _ScriptedPeer([[]])})

        async def cancelling_sleeper(seconds: float) -> None:
            del seconds
            raise asyncio.CancelledError

        with caplog.at_level(logging.WARNING, logger="synapse_channel.core.multihub_watch"):
            with pytest.raises(asyncio.CancelledError):
                await watch.run(sleeper=cancelling_sleeper, rounds=None)
        assert "multihub watch stopped" not in caplog.text


class TestParseWatchPins:
    def test_parses_pins_for_watched_peers(self) -> None:
        peers = {"hub-b": "wss://b:443", "hub-c": "wss://c:443"}
        parsed = parse_watch_pins(["hub-b=sha256:" + "a" * 64, " hub-c = SHA256:BB "], peers)
        assert parsed == {"hub-b": "sha256:" + "a" * 64, "hub-c": "SHA256:BB"}

    def test_no_pins_yields_an_empty_map(self) -> None:
        assert parse_watch_pins([], {"hub-b": "wss://b:443"}) == {}

    def test_rejects_malformed_values(self) -> None:
        peers = {"hub-b": "wss://b:443"}
        for value in ("hub-b", "=sha256:aa", "hub-b=", "hub-b=md5:aa"):
            with pytest.raises(ValueError, match="--multihub-watch-pin"):
                parse_watch_pins([value], peers)

    def test_rejects_an_unwatched_peer(self) -> None:
        with pytest.raises(ValueError, match="does not watch"):
            parse_watch_pins(["ghost=sha256:aa"], {"hub-b": "wss://b:443"})

    def test_rejects_a_repeated_peer(self) -> None:
        peers = {"hub-b": "wss://b:443"}
        with pytest.raises(ValueError, match="twice"):
            parse_watch_pins(["hub-b=sha256:aa", "hub-b=sha256:bb"], peers)


class TestWatchPinnedTransport:
    def test_only_pinned_peers_receive_a_connector(self) -> None:
        captured: dict[str, dict[str, object]] = {}

        def factory(
            uri: str, *, local_id: str, token: str | None = None, **extra: object
        ) -> EventFetcher:
            captured[uri] = {"local_id": local_id, "token": token, **extra}

            async def fetch(after_seq: int) -> Sequence[StoredEvent]:
                del after_seq
                return []  # pragma: no cover - construction-only test

            return fetch

        MultiHubWatch(
            {"hub-b": "wss://b:443", "hub-c": "ws://c:8876"},
            local_id="syn-a",
            fetcher_factory=factory,
            pins={"hub-b": "sha256:" + "a" * 64},
        )

        assert callable(captured["wss://b:443"]["connector"])
        assert "connector" not in captured["ws://c:8876"]
