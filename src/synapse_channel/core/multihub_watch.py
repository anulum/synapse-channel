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
from synapse_channel.core.multihub_transport import MultiHubFetchError, network_fetcher

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
    ) -> None:
        self.interval = max(float(interval), MIN_WATCH_INTERVAL)
        self._namespace_of = namespace_of
        self._follower = follower if follower is not None else MultiHubFollower()
        self._fetchers: dict[str, EventFetcher] = {
            peer: fetcher_factory(uri, local_id=local_id, token=token)
            for peer, uri in peers.items()
        }
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

        Parameters
        ----------
        sleeper : Sleeper, optional
            The delay primitive; injected in tests so rounds run without real time.
        rounds : int or None, optional
            Stop after this many rounds; ``None`` (the production default) runs until the
            surrounding task is cancelled.
        """
        completed = 0
        while True:
            await self.poll_once()
            completed += 1
            if rounds is not None and completed >= rounds:
                return
            await sleeper(self.interval)
