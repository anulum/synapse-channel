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
from synapse_channel.core.journal import (
    EventKind,
    record_multihub_ownership_transitions,
    restore_active_multihub_partitions,
)
from synapse_channel.core.multihub_follower import EventFetcher, MultiHubFollower
from synapse_channel.core.multihub_transport import (
    MultiHubFetchError,
    network_fetcher,
    pinned_connector,
)
from synapse_channel.core.namespace_ownership import NamespaceOwnership, OwnershipOutcome
from synapse_channel.core.persistence import EventStore

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
    namespace_ownership : NamespaceOwnership or None, optional
        Static ownership map used to distinguish a real contest from the
        configured remote owner asserting its own namespace.  Required for
        durable partition/heal evidence; observation still works when absent.
    journal : EventStore or None, optional
        Durable audit store.  When both this and ``namespace_ownership`` are
        present, entering, changing, and healing a contested namespace is
        committed at ``FULL`` durability.
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
        namespace_ownership: NamespaceOwnership | None = None,
        journal: EventStore | None = None,
    ) -> None:
        self.interval = max(float(interval), MIN_WATCH_INTERVAL)
        self._namespace_of = namespace_of
        self._follower = follower if follower is not None else MultiHubFollower()
        self._namespace_ownership = namespace_ownership
        self._journal = journal
        self._fetchers: dict[str, EventFetcher] = {}
        for peer, uri in peers.items():
            extra: dict[str, object] = {}
            if pins and peer in pins:
                # A pinned wss:// peer is trusted by certificate pin, not CA chain;
                # the connector fails the poll closed on any mismatch.
                extra["connector"] = pinned_connector(pins[peer])
            self._fetchers[peer] = fetcher_factory(uri, local_id=local_id, token=token, **extra)
        self._partitioned = (
            restore_active_multihub_partitions(journal)
            if journal is not None and namespace_ownership is not None
            else {}
        )
        # An unresolved durable partition must refuse immediately after restart,
        # before the first peer poll.  Seed the live assertion feed from the
        # persisted contestants; a successful observation round may later heal it.
        self._assertions: dict[str, frozenset[str]] = {
            namespace: frozenset(contesting) for namespace, contesting in self._partitioned.items()
        }

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
        failed: set[str] = set()
        for peer, fetch in self._fetchers.items():
            try:
                await self._follower.poll(peer, fetch)
            except MultiHubFetchError as exc:
                outcomes[peer] = str(exc)
                failed.add(peer)
                logger.warning("multihub watch: poll of peer %r failed: %s", peer, exc)
                continue
            outcomes[peer] = None
            answered = True
        if answered:
            observed = self._follower.asserting_owners(project_of=self._namespace_of)
            refreshed = dict(observed)
            # After restart the follower's in-memory event union is empty, but
            # unresolved partition contestants were restored from the journal.
            # If one of those peers fails while another peer answers, or returns
            # no durable history at all, preserve its suspicion. Partial/empty
            # success must not fabricate a heal for a contestant whose release
            # was never re-observed.
            for namespace, contesting in self._partitioned.items():
                retained = {
                    peer
                    for peer in contesting
                    if peer in self._fetchers
                    and (peer in failed or self._follower.cursor(peer) == 0)
                }
                if retained:
                    refreshed[namespace] = frozenset(
                        set(refreshed.get(namespace, frozenset())) | retained
                    )
            self._record_ownership_transitions(refreshed)
            self._assertions = refreshed
        return outcomes

    def _record_ownership_transitions(self, refreshed: Mapping[str, frozenset[str]]) -> None:
        """Durably record changed partition state before publishing the new feed.

        Only a successful observation round reaches this method.  Consequently
        an unreachable peer cannot manufacture a heal: failed rounds retain the
        old assertion view and the restored unresolved-partition set.
        """
        ownership = self._namespace_ownership
        journal = self._journal
        if ownership is None or journal is None:
            return
        transitions: list[tuple[str, Mapping[str, object]]] = []
        next_partitioned = dict(self._partitioned)
        namespaces = set(refreshed) | set(self._partitioned)
        for namespace in sorted(namespaces):
            decision = ownership.resolve(
                namespace, asserting_hubs=refreshed.get(namespace, frozenset())
            )
            if decision.outcome is OwnershipOutcome.PARTITIONED:
                contesting = tuple(decision.contesting)
                if self._partitioned.get(namespace) == contesting:
                    continue
                transitions.append(
                    (
                        EventKind.MULTIHUB_PARTITION,
                        {
                            "namespace": namespace,
                            "local_hub_id": ownership.local_hub_id,
                            "owner_hub_id": ownership.owner_of(namespace) or "",
                            "contesting_hubs": list(contesting),
                            "outcome": OwnershipOutcome.PARTITIONED.value,
                            "transition": (
                                "entered" if namespace not in self._partitioned else "updated"
                            ),
                        },
                    )
                )
                next_partitioned[namespace] = contesting
                continue
            previous = self._partitioned.get(namespace)
            if previous is None:
                continue
            transitions.append(
                (
                    EventKind.MULTIHUB_HEAL,
                    {
                        "namespace": namespace,
                        "local_hub_id": ownership.local_hub_id,
                        "owner_hub_id": decision.owner_hub_id or "",
                        "previous_contesting_hubs": list(previous),
                        "outcome": decision.outcome.value,
                        "observation_refreshed": True,
                    },
                )
            )
            next_partitioned.pop(namespace, None)
        record_multihub_ownership_transitions(journal, transitions)
        self._partitioned = next_partitioned

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
