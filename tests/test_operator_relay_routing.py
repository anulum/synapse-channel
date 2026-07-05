# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — origin-side routing policy for a cross-hub operator relay

from __future__ import annotations

from synapse_channel.core.namespace_ownership import NamespaceOwnership, OwnershipDecision
from synapse_channel.core.operator_relay_routing import (
    NO_RELAY_ROUTE,
    RelayRouteKind,
    route_operator_relay,
)
from synapse_channel.core.operator_relay_transport import OperatorRelayPeer

_NAMESPACE = "OWNED"
_OWNER = "syn-owner"
_LOCAL = "syn-edge"
_PEERS = {_OWNER: OperatorRelayPeer(uri="ws://owner/", token="tok")}


def _resolve(owners: dict[str, str], *, asserting: tuple[str, ...] = ()) -> OwnershipDecision:
    """Resolve the target namespace against an ownership map, folding asserting hubs."""
    ownership = NamespaceOwnership(owners=owners, local_hub_id=_LOCAL)
    return ownership.resolve(_NAMESPACE, asserting_hubs=asserting)


def test_a_locally_owned_namespace_applies_here() -> None:
    route = route_operator_relay(_resolve({_NAMESPACE: _LOCAL}), relay_peers=_PEERS)
    assert route.kind is RelayRouteKind.APPLY_LOCAL
    assert route.peer is None
    assert route.reason == ""


def test_a_remote_owned_namespace_with_a_route_forwards_to_the_owner() -> None:
    route = route_operator_relay(_resolve({_NAMESPACE: _OWNER}), relay_peers=_PEERS)
    assert route.kind is RelayRouteKind.FORWARD
    assert route.peer is not None
    assert route.peer.uri == "ws://owner/"
    assert route.peer.token == "tok"


def test_a_remote_owned_namespace_without_a_route_is_refused() -> None:
    route = route_operator_relay(_resolve({_NAMESPACE: _OWNER}), relay_peers=None)
    assert route.kind is RelayRouteKind.REFUSE
    assert route.reason == NO_RELAY_ROUTE
    assert route.peer is None


def test_a_remote_owner_absent_from_the_route_map_is_refused() -> None:
    # The owner is named, but the (non-empty) route map has no entry for it.
    other = {"syn-someone-else": OperatorRelayPeer(uri="ws://x/")}
    route = route_operator_relay(_resolve({_NAMESPACE: _OWNER}), relay_peers=other)
    assert route.kind is RelayRouteKind.REFUSE
    assert route.reason == NO_RELAY_ROUTE


def test_an_ungoverned_namespace_is_refused_with_the_outcome_reason() -> None:
    route = route_operator_relay(_resolve({}), relay_peers=_PEERS)
    assert route.kind is RelayRouteKind.REFUSE
    assert route.reason == "ungoverned"


def test_a_partitioned_namespace_is_refused_with_the_outcome_reason() -> None:
    # This hub believes it owns the namespace, but a peer is observed asserting the same.
    route = route_operator_relay(
        _resolve({_NAMESPACE: _LOCAL}, asserting=("syn-contender",)), relay_peers=_PEERS
    )
    assert route.kind is RelayRouteKind.REFUSE
    assert route.reason == "partitioned"
