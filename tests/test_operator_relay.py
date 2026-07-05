# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — deny-by-default policy for governed cross-hub operator relays

from __future__ import annotations

from synapse_channel.core import acl
from synapse_channel.core.federation import ScopeGrant
from synapse_channel.core.operator_relay import (
    RELAY_AUTHORISED,
    RELAY_RELEASE,
    RELAYABLE_ACTIONS,
    RelayDenyReason,
    authorise_relay,
)
from synapse_channel.core.operator_relay_wire import RelayActionRequest

_NAMESPACE = "SYNAPSE-CHANNEL"


def _request(
    action: str = RELAY_RELEASE, namespace: str = _NAMESPACE, *, reason: str = ""
) -> RelayActionRequest:
    return RelayActionRequest(
        action=action,
        namespace=namespace,
        task_id="t1",
        operator="ops-admin",
        origin_hub_id="syn-a",
        reason=reason,
    )


def _release_scope(namespace: str = _NAMESPACE) -> tuple[ScopeGrant, ...]:
    return (ScopeGrant(verb=acl.RELEASE, namespace=namespace),)


# --- the registry ------------------------------------------------------------------------


def test_release_is_the_registered_action_and_maps_to_the_release_permission() -> None:
    action = RELAYABLE_ACTIONS[RELAY_RELEASE]
    assert action.action_id == RELAY_RELEASE
    assert action.verb == acl.RELEASE
    assert action.requires_ownership is True


# --- authorise_relay: deny-by-default at every layer -------------------------------------


def test_authorised_when_peer_scope_and_ownership_all_permit() -> None:
    decision = authorise_relay(
        _request(), peer_authorised=True, scope=_release_scope(), owns_namespace=True
    )
    assert decision.allowed is True
    assert decision.reason == RELAY_AUTHORISED
    assert decision.action == RELAY_RELEASE


def test_refuses_an_unverified_peer_before_anything_else() -> None:
    # An unknown action AND an unverified peer must report the peer failure, so an
    # unauthenticated caller never learns whether the action or namespace is valid.
    decision = authorise_relay(
        _request(action="bogus"), peer_authorised=False, scope=(), owns_namespace=True
    )
    assert decision.allowed is False
    assert decision.reason == RelayDenyReason.PEER_NOT_AUTHORISED


def test_refuses_an_unregistered_action() -> None:
    decision = authorise_relay(
        _request(action="delete-everything"),
        peer_authorised=True,
        scope=_release_scope(),
        owns_namespace=True,
    )
    assert decision.allowed is False
    assert decision.reason == RelayDenyReason.UNKNOWN_ACTION


def test_refuses_when_the_scope_does_not_grant_the_verb_in_the_namespace() -> None:
    decision = authorise_relay(
        _request(),
        peer_authorised=True,
        scope=_release_scope(namespace="OTHER-NAMESPACE"),
        owns_namespace=True,
    )
    assert decision.allowed is False
    assert decision.reason == RelayDenyReason.SCOPE_NOT_GRANTED


def test_refuses_when_the_scope_grants_a_different_verb() -> None:
    decision = authorise_relay(
        _request(),
        peer_authorised=True,
        scope=(ScopeGrant(verb=acl.CLAIM, namespace=_NAMESPACE),),
        owns_namespace=True,
    )
    assert decision.allowed is False
    assert decision.reason == RelayDenyReason.SCOPE_NOT_GRANTED


def test_refuses_on_an_empty_scope() -> None:
    decision = authorise_relay(_request(), peer_authorised=True, scope=(), owns_namespace=True)
    assert decision.allowed is False
    assert decision.reason == RelayDenyReason.SCOPE_NOT_GRANTED


def test_refuses_a_state_mutation_when_the_hub_does_not_own_the_namespace() -> None:
    decision = authorise_relay(
        _request(), peer_authorised=True, scope=_release_scope(), owns_namespace=False
    )
    assert decision.allowed is False
    assert decision.reason == RelayDenyReason.NAMESPACE_NOT_OWNED


def test_refuses_a_reasonless_relay_when_a_reason_is_required() -> None:
    decision = authorise_relay(
        _request(reason="   "),  # blank after stripping is no reason at all
        peer_authorised=True,
        scope=_release_scope(),
        owns_namespace=True,
        require_reason=True,
    )
    assert decision.allowed is False
    assert decision.reason == RelayDenyReason.REASON_REQUIRED


def test_authorises_a_relay_with_a_reason_when_a_reason_is_required() -> None:
    decision = authorise_relay(
        _request(reason="freeing a wedged release"),
        peer_authorised=True,
        scope=_release_scope(),
        owns_namespace=True,
        require_reason=True,
    )
    assert decision.allowed is True
    assert decision.reason == RELAY_AUTHORISED


def test_a_missing_reason_is_ignored_when_no_reason_is_required() -> None:
    decision = authorise_relay(
        _request(), peer_authorised=True, scope=_release_scope(), owns_namespace=True
    )
    assert decision.allowed is True  # default off: a reasonless relay still authorises
