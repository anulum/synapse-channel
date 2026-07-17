# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the hub's own standing follower feeding partition detection
"""The hub's own standing follower, feeding partition detection from live peers.

Runtime partition detection already ships opt-in: a hub wired with an
``observed_asserting_hubs`` feed resolves a namespace a peer is observed contesting to
*partitioned* and refuses the claim (:mod:`synapse_channel.core.namespace_ownership`), and
:func:`~synapse_channel.core.multihub_fold.asserting_owners` builds that feed from a
follower's observed claims. Until now the operator wired the feed by hand; this module is
the missing standing half: a :class:`MultiHubWatch` polls each configured peer hub over the
multi-hub pull (:func:`~synapse_channel.core.multihub_transport.network_fetcher` into a
:class:`~synapse_channel.core.multihub_follower.MultiHubFollower`), folds the observed
claims, and exposes the per-namespace asserting-hub view the ownership gate consumes.

The watch is **opt-in by construction**: a hub only runs one when the operator names each
peer explicitly (``synapse hub --multihub-watch PEER=URI``). That flag *is* the operator
confirmation the multi-hub docs require for an always-on outbound connection from the hub —
nothing is discovered, and no peer is polled that was not named.

Failure posture is **fail-closed for authority**: a failed poll keeps the *last successful*
observation instead of clearing it. An observation only ever narrows what the hub grants —
a hub observed asserting a namespace marks it contested — so serving stale observations
during a link outage errs on the refusing side, while clearing them would let a partitioned
namespace silently resume granting the moment the link (and therefore the evidence) drops.
Stale observation is never authority; it is retained suspicion.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping

from synapse_channel.core.acl_enforcement import project_of
from synapse_channel.core.multihub_fold import asserting_owners
from synapse_channel.core.multihub_follower import EventFetcher, MultiHubFollower
from synapse_channel.core.multihub_transport import (
    MultiHubFetchError,
    network_fetcher,
    pinned_connector,
)

logger = logging.getLogger(__name__)

DEFAULT_WATCH_INTERVAL = 30.0
"""Seconds between poll rounds; bounded below by :data:`MIN_WATCH_INTERVAL`."""

MIN_WATCH_INTERVAL = 1.0
"""Smallest accepted poll interval, so a typo cannot busy-loop the hub against its peers."""

FetcherFactory = Callable[..., EventFetcher]
Sleeper = Callable[[float], Awaitable[None]]


def parse_watch_peers(values: list[str]) -> dict[str, str]:
    """Parse repeatable ``PEER=URI`` CLI values into a peer-id-to-URI map.

    Parameters
    ----------
    values : list[str]
        Raw flag values, each ``PEER=URI`` — the peer hub id the observations are
        attributed to, and the websocket URI it is polled at.

    Returns
    -------
    dict[str, str]
        Peer hub id to URI, in the order given.

    Raises
    ------
    ValueError
        If a value has no ``=``, an empty peer id or URI, or repeats a peer id.
    """
    peers: dict[str, str] = {}
    for value in values:
        peer, sep, uri = value.partition("=")
        peer, uri = peer.strip(), uri.strip()
        if not sep or not peer or not uri:
            msg = f"--multihub-watch must use PEER=URI, got {value!r}"
            raise ValueError(msg)
        if peer in peers:
            msg = f"--multihub-watch names peer {peer!r} twice"
            raise ValueError(msg)
        peers[peer] = uri
    return peers


def parse_watch_pins(values: list[str], peers: Mapping[str, str]) -> dict[str, str]:
    """Parse repeatable ``PEER=sha256:<hex>`` CLI values into a peer-to-pin map.

    Parameters
    ----------
    values : list[str]
        Raw flag values, each ``PEER=sha256:<hex>`` — the watched peer the pin
        applies to and the SHA-256 certificate pin its ``wss://`` connection must
        present.
    peers : Mapping[str, str]
        The watched peers (from :func:`parse_watch_peers`); a pin naming an
        unwatched peer is an operator mistake and is refused.

    Returns
    -------
    dict[str, str]
        Peer hub id to certificate pin.

    Raises
    ------
    ValueError
        If a value has no ``=``, an empty peer or pin, a pin not in
        ``sha256:<hex>`` form, a peer not named by ``--multihub-watch``, or a
        repeated peer.
    """
    pins: dict[str, str] = {}
    for value in values:
        peer, sep, pin = value.partition("=")
        peer, pin = peer.strip(), pin.strip()
        if not sep or not peer or not pin:
            msg = f"--multihub-watch-pin must use PEER=sha256:<hex>, got {value!r}"
            raise ValueError(msg)
        if not pin.lower().startswith("sha256:"):
            msg = f"--multihub-watch-pin pin must be sha256:<hex>, got {pin!r}"
            raise ValueError(msg)
        if peer not in peers:
            msg = f"--multihub-watch-pin names {peer!r}, which --multihub-watch does not watch"
            raise ValueError(msg)
        if peer in pins:
            msg = f"--multihub-watch-pin names peer {peer!r} twice"
            raise ValueError(msg)
        pins[peer] = pin
    return pins


class MultiHubWatch:
    """Poll named peer hubs and hold the observed asserting-owners view.

    Parameters
    ----------
    peers : Mapping[str, str]
        Peer hub id to websocket URI; every peer here was named explicitly by the operator.
    local_id : str
        The identity stamped on each poll's request frame.
    token : str or None, optional
        Authentication token for secured peers; ``None`` sends no token.
    interval : float, optional
        Seconds between poll rounds, clamped to at least :data:`MIN_WATCH_INTERVAL`.
    namespace_of : Callable[[str], str], optional
        Maps an observed claim owner to its namespace; defaults to the same
        :func:`~synapse_channel.core.acl_enforcement.project_of` derivation the ownership
        gate uses, so the watch and the gate can never disagree about a claim's namespace.
    follower : MultiHubFollower or None, optional
        The cursored fold; a fresh one when ``None``. Injected in tests.
    fetcher_factory : FetcherFactory, optional
        Builds the per-peer transport; defaults to the real network fetcher. Injected in
        tests to script peers without sockets.
    pins : Mapping[str, str] or None, optional
        Per-peer ``sha256:<hex>`` certificate pins (see
        :func:`parse_watch_pins`). A pinned peer's ``wss://`` connection is
        trusted by live certificate pin instead of CA chain; a mismatch fails
        that poll closed. Peers absent from the map use the default transport.
    """

    def __init__(
        self,
        peers: Mapping[str, str],
        *,
        local_id: str,
        token: str | None = None,
        interval: float = DEFAULT_WATCH_INTERVAL,
        namespace_of: Callable[[str], str] = project_of,
        follower: MultiHubFollower | None = None,
        fetcher_factory: FetcherFactory = network_fetcher,
        pins: Mapping[str, str] | None = None,
    ) -> None:
        self.interval = max(float(interval), MIN_WATCH_INTERVAL)
        self._namespace_of = namespace_of
        self._follower = follower if follower is not None else MultiHubFollower()
        self._fetchers: dict[str, EventFetcher] = {}
        for peer, uri in peers.items():
            extra: dict[str, object] = {}
            if pins and peer in pins:
                # A pinned wss:// peer is trusted by certificate pin, not CA chain;
                # the connector fails the poll closed on any mismatch.
                extra["connector"] = pinned_connector(pins[peer])
            self._fetchers[peer] = fetcher_factory(uri, local_id=local_id, token=token, **extra)
        self._assertions: dict[str, frozenset[str]] = {}

    def observed_asserting_hubs(self, namespace: str) -> tuple[str, ...]:
        """Return the hub ids last observed asserting authority over ``namespace``.

        This is the feed :meth:`~synapse_channel.core.namespace_ownership.NamespaceOwnership.
        resolve` consumes; it reflects the most recent successful fold, so during a peer
        outage it keeps refusing what was last seen contested rather than forgetting it.
        """
        return tuple(sorted(self._assertions.get(namespace, frozenset())))

    async def poll_once(self) -> dict[str, str | None]:
        """Poll every configured peer once and refresh the asserting-owners view.

        Each peer is polled independently: one unreachable peer neither blocks nor clears
        what the others (or its own last successful poll) contributed — the follower keeps
        the accumulated union, so the fold after a partial round still carries every event
        seen so far. The view is recomputed only when at least one peer answered; an
        entirely failed round leaves the previous observation untouched.

        Returns
        -------
        dict[str, str | None]
            Per peer, ``None`` on success or the failure message — the health surface a
            caller (or a log line) can report without parsing logs.
        """
        outcomes: dict[str, str | None] = {}
        answered = False
        for peer, fetch in self._fetchers.items():
            try:
                await self._follower.poll(peer, fetch)
            except MultiHubFetchError as exc:
                outcomes[peer] = str(exc)
                logger.warning("multihub watch: poll of peer %r failed: %s", peer, exc)
                continue
            outcomes[peer] = None
            answered = True
        if answered:
            self._assertions = asserting_owners(
                self._follower.observed(), project_of=self._namespace_of
            )
        return outcomes

    async def run(self, *, sleeper: Sleeper = asyncio.sleep, rounds: int | None = None) -> None:
        """Poll on the bounded interval until cancelled (or for ``rounds`` rounds in tests).

        Each round's per-peer faults are already absorbed by :meth:`poll_once` — an
        unreachable or malformed peer surfaces as :class:`MultiHubFetchError`, is logged, and
        is left to the next round — so under normal operation the loop runs indefinitely and
        stops only on cancellation, the standing task's ordinary shutdown. Any *other*
        exception escaping a round is unexpected (a programming fault, not a peer being
        unreachable); it is logged at ``WARNING`` before it propagates, so an operator sees the
        watch stop instead of the task dying silently and partition detection freezing on the
        last observation with no signal. Cancellation is not an error and passes through
        without such a log.

        Parameters
        ----------
        sleeper : Sleeper, optional
            The delay primitive; injected in tests so rounds run without real time.
        rounds : int or None, optional
            Stop after this many rounds; ``None`` (the production default) runs until the
            surrounding task is cancelled.
        """
        completed = 0
        try:
            while True:
                await self.poll_once()
                completed += 1
                if rounds is not None and completed >= rounds:
                    return
                await sleeper(self.interval)
        except Exception as exc:
            # ``asyncio.CancelledError`` is a ``BaseException``, not ``Exception``, so ordinary
            # shutdown never reaches here; only a genuine fault does. Log, then re-raise so the
            # task still carries the failure for the driver's shutdown ``await``.
            logger.warning("multihub watch stopped on an unexpected error: %s", exc)
            raise
