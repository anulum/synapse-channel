# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — opt-in advisory observed peer state for operator surfaces
"""Fetch and render advisory observed state from peer hubs.

The local hub remains the only authority for local claims. Observed peers are an
operator view over remote event logs: each peer is fetched through the multi-hub
log-request path, folded with :mod:`synapse_channel.core.multihub_fold`, and
marked ``observed@<hub>`` wherever it is rendered. A failed peer fetch reports a
peer row with the error; it never blocks or mutates the local state snapshot.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol, cast

from synapse_channel.core.clock_skew import ClockSkew
from synapse_channel.core.multihub_fold import ObservedState, fold_observed_state
from synapse_channel.core.multihub_merge import tag_events
from synapse_channel.core.multihub_transport import MultiHubFetchError, network_fetcher
from synapse_channel.core.persistence import StoredEvent


@dataclass(frozen=True)
class ObservedPeerSpec:
    """A named peer hub to include in an opt-in observed view.

    Parameters
    ----------
    hub_id : str
        Operator-facing hub id used in ``observed@<hub>`` labels.
    uri : str
        WebSocket URI for the peer hub's multi-hub log serving path.
    """

    hub_id: str
    uri: str


@dataclass(frozen=True)
class ObservedPeerSnapshot:
    """One peer's advisory observed-state snapshot.

    Parameters
    ----------
    hub_id, uri : str
        Peer identity and URI that were requested.
    reachable : bool
        Whether the peer fetch succeeded.
    cursor : int
        Highest event sequence folded from this peer. ``0`` when unreachable or
        empty.
    log_end_seq : int or None
        Peer log high-water from the serving hub, if the peer reports it.
    state : ObservedState
        Folded advisory board/progress/claim view. Empty when unreachable.
    error : str
        Human-readable failure detail for an unreachable peer.
    clock_skew_seconds : float or None
        Local-minus-peer clock skew measured from the peer welcome timestamp.
    """

    hub_id: str
    uri: str
    reachable: bool
    cursor: int = 0
    log_end_seq: int | None = None
    state: ObservedState = field(default_factory=ObservedState)
    error: str = ""
    clock_skew_seconds: float | None = None

    @property
    def lag(self) -> int | None:
        """Return ``log_end_seq - cursor`` when the peer exposes its high-water."""
        if self.log_end_seq is None:
            return None
        return max(0, self.log_end_seq - self.cursor)

    @property
    def observed_agents(self) -> tuple[str, ...]:
        """Return agent identities inferred from observed claim owners.

        The event log is not a live roster, so this is intentionally conservative:
        only claim owners present in the observed claim fold are listed.
        """
        names = {
            str(claim.claim.get("owner", "")).strip()
            for claim in self.state.observed_claims.values()
        }
        return tuple(sorted(name for name in names if name))

    def to_dict(self) -> dict[str, object]:
        """Return a stable JSON-compatible object for CLI and dashboard outputs."""
        return {
            "hub_id": self.hub_id,
            "uri": self.uri,
            "reachable": self.reachable,
            "cursor": self.cursor,
            "log_end_seq": self.log_end_seq,
            "lag": self.lag,
            "observed_agents": list(self.observed_agents),
            "clock_skew_seconds": self.clock_skew_seconds,
            "state": self.state.to_dict(),
            "error": self.error,
        }


class ObservedFetcher(Protocol):
    """Fetches a peer event batch and may expose the peer's log high-water."""

    last_log_end_seq: int | None
    last_clock_skew: ClockSkew | None

    async def __call__(self, after_seq: int) -> Sequence[StoredEvent]:
        """Fetch events after ``after_seq``."""
        ...  # pragma: no cover - protocol body has no runtime path


ObservedFetcherFactory = Callable[[ObservedPeerSpec], ObservedFetcher]
"""Factory for peer fetchers; injectable for tests."""


def parse_observed_peer(value: str) -> ObservedPeerSpec:
    """Parse a ``HUB=URI`` observed-peer CLI argument.

    Raises
    ------
    ValueError
        If the peer id or URI is missing.
    """
    hub_id, sep, uri = value.partition("=")
    hub_id = hub_id.strip()
    uri = uri.strip()
    if not sep or not hub_id or not uri:
        raise ValueError("observed peer must be HUB=URI")
    return ObservedPeerSpec(hub_id=hub_id, uri=uri)


def parse_observed_peers(values: Sequence[str] | None) -> tuple[ObservedPeerSpec, ...]:
    """Parse zero or more observed-peer arguments."""
    return tuple(parse_observed_peer(value) for value in (values or ()))


def network_observed_fetcher_factory(
    *,
    local_id: str,
    token: str | None = None,
    timeout: float = 10.0,
) -> ObservedFetcherFactory:
    """Return a factory that fetches peers through the multi-hub network transport."""

    def build(spec: ObservedPeerSpec) -> ObservedFetcher:
        fetcher = network_fetcher(
            spec.uri,
            local_id=local_id,
            token=token,
            timeout=timeout,
        )
        return cast(ObservedFetcher, fetcher)

    return build


async def fetch_observed_peer(
    spec: ObservedPeerSpec,
    *,
    fetcher_factory: ObservedFetcherFactory,
) -> ObservedPeerSnapshot:
    """Fetch and fold one peer into an advisory snapshot."""
    fetcher = fetcher_factory(spec)
    try:
        events = await fetcher(0)
    except (MultiHubFetchError, OSError, TimeoutError) as exc:
        return ObservedPeerSnapshot(
            hub_id=spec.hub_id,
            uri=spec.uri,
            reachable=False,
            error=str(exc),
        )
    cursor = max((int(event.seq) for event in events), default=0)
    state = fold_observed_state(tag_events(spec.hub_id, events))
    return ObservedPeerSnapshot(
        hub_id=spec.hub_id,
        uri=spec.uri,
        reachable=True,
        cursor=cursor,
        log_end_seq=fetcher.last_log_end_seq,
        clock_skew_seconds=None
        if fetcher.last_clock_skew is None
        else fetcher.last_clock_skew.seconds,
        state=state,
    )


async def fetch_observed_peers(
    specs: Sequence[ObservedPeerSpec],
    *,
    fetcher_factory: ObservedFetcherFactory,
) -> tuple[ObservedPeerSnapshot, ...]:
    """Fetch every named peer concurrently and return snapshots in input order."""
    if not specs:
        return ()
    return tuple(
        await asyncio.gather(
            *(fetch_observed_peer(spec, fetcher_factory=fetcher_factory) for spec in specs)
        )
    )


def observed_peers_to_dict(peers: Sequence[ObservedPeerSnapshot]) -> list[dict[str, object]]:
    """Return JSON-compatible peer snapshots."""
    return [peer.to_dict() for peer in peers]


def observed_claim_count(peers: Sequence[ObservedPeerSnapshot]) -> int:
    """Return the total number of currently observed peer claims."""
    return sum(len(peer.state.observed_claims) for peer in peers if peer.reachable)


def observed_max_lag(peers: Sequence[ObservedPeerSnapshot]) -> int | None:
    """Return the maximum known cursor lag across reachable peers."""
    lags = [peer.lag for peer in peers if peer.reachable and peer.lag is not None]
    return max(lags) if lags else None


def observed_max_abs_clock_skew(peers: Sequence[ObservedPeerSnapshot]) -> float | None:
    """Return the largest absolute known clock skew across reachable peers."""
    skews = [
        peer.clock_skew_seconds
        for peer in peers
        if peer.reachable and peer.clock_skew_seconds is not None
    ]
    return max(skews, key=abs) if skews else None
