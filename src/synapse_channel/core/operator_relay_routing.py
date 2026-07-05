# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — origin-side routing policy for a governed cross-hub operator relay
"""Origin-side routing policy for a governed operator relay: apply here, forward, or refuse.

The serving policy (:mod:`synapse_channel.core.operator_relay`) decides whether a relay a hub
*receives* may be applied; this module is its origin-side counterpart, deciding where a relay a
hub *originates* must go. It is pure and I/O-free — it turns a namespace-ownership resolution
plus the configured relay routes into one of three routes, and the collaborator that owns the
sockets (:class:`~synapse_channel.core.operator_relay_forwarding.OperatorRelayForwarding`) acts
on it.

The routing mirrors cross-hub claim routing (:mod:`synapse_channel.core.namespace_ownership`),
deny-by-default at the same seams: a hub applies a relay only for a namespace it authoritatively
owns, forwards a namespace a named peer owns *only* when a relay route to that peer is
configured, and refuses everything else — an unowned namespace with no route, an ungoverned
namespace, or a partitioned one — fail-closed, so a relay never slips through to a hub the
operator never authorised.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from synapse_channel.core.namespace_ownership import OwnershipDecision, OwnershipOutcome
from synapse_channel.core.operator_relay_transport import OperatorRelayPeer

NO_RELAY_ROUTE = "no_relay_route"
"""Refusal reason: the namespace is owned by a peer this hub has no relay route to."""


class RelayRouteKind(Enum):
    """Where an originated operator relay must go, resolved from namespace ownership."""

    APPLY_LOCAL = "apply_local"
    """This hub owns the namespace, so it applies the relay itself (the serving path)."""

    FORWARD = "forward"
    """A named peer owns the namespace and a relay route to it is configured; relay onward."""

    REFUSE = "refuse"
    """No hub this can reach may apply the relay; refuse fail-closed with the reason."""


@dataclass(frozen=True, slots=True)
class RelayRoute:
    """The resolved route for one originated operator relay.

    Attributes
    ----------
    kind : RelayRouteKind
        Whether to apply the relay locally, forward it to the owning peer, or refuse it.
    peer : OperatorRelayPeer or None
        The route to the owning hub on :attr:`RelayRouteKind.FORWARD`; ``None`` otherwise.
    reason : str
        On :attr:`RelayRouteKind.REFUSE`, why — :data:`NO_RELAY_ROUTE` for an unrouted owner,
        or the :class:`~synapse_channel.core.namespace_ownership.OwnershipOutcome` value for an
        ungoverned or partitioned namespace. Empty for the two non-refusing routes.
    """

    kind: RelayRouteKind
    peer: OperatorRelayPeer | None = None
    reason: str = ""


def route_operator_relay(
    decision: OwnershipDecision, *, relay_peers: Mapping[str, OperatorRelayPeer] | None
) -> RelayRoute:
    """Resolve where an originated operator relay must go, deny-by-default.

    Parameters
    ----------
    decision : OwnershipDecision
        The namespace-ownership resolution for the relay's target namespace, already folded
        against any observed asserting hubs so a partition forces a refusal.
    relay_peers : Mapping[str, OperatorRelayPeer] or None
        Relay routes keyed by owning hub id; ``None`` (or a missing entry) forwards nothing, so
        a remote-owned relay with no route is refused with the owner named.

    Returns
    -------
    RelayRoute
        :attr:`RelayRouteKind.APPLY_LOCAL` when this hub owns the namespace,
        :attr:`RelayRouteKind.FORWARD` with the peer route when a named peer owns it and a route
        exists, or :attr:`RelayRouteKind.REFUSE` with the reason for every other outcome.
    """
    if decision.grants_locally:
        return RelayRoute(RelayRouteKind.APPLY_LOCAL)
    if decision.outcome is OwnershipOutcome.REMOTE:
        owner = decision.owner_hub_id or ""
        peer = relay_peers.get(owner) if relay_peers else None
        if peer is not None:
            return RelayRoute(RelayRouteKind.FORWARD, peer=peer)
        return RelayRoute(RelayRouteKind.REFUSE, reason=NO_RELAY_ROUTE)
    return RelayRoute(RelayRouteKind.REFUSE, reason=decision.outcome.value)
