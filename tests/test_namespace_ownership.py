# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — namespace ownership resolution behind claim routing

from __future__ import annotations

from synapse_channel.core.namespace_ownership import (
    NamespaceOwnership,
    OwnershipOutcome,
)

_LOCAL = "syn-local"
_PEER = "syn-peer"
_OTHER = "syn-other"
_NS = "SYNAPSE-CHANNEL"


def _ownership(**owners: str) -> NamespaceOwnership:
    return NamespaceOwnership(owners=dict(owners), local_hub_id=_LOCAL)


def test_local_owner_grants_locally() -> None:
    decision = _ownership(**{_NS: _LOCAL}).resolve(_NS)
    assert decision.outcome is OwnershipOutcome.LOCAL
    assert decision.owner_hub_id == _LOCAL
    assert decision.grants_locally is True
    assert decision.refuses is False
    assert decision.contesting == ()


def test_remote_owner_routes_to_the_peer() -> None:
    decision = _ownership(**{_NS: _PEER}).resolve(_NS)
    assert decision.outcome is OwnershipOutcome.REMOTE
    assert decision.owner_hub_id == _PEER
    assert decision.grants_locally is False
    assert decision.refuses is False


def test_an_ungoverned_namespace_fails_closed() -> None:
    decision = _ownership(**{"OTHER-NS": _LOCAL}).resolve(_NS)
    assert decision.outcome is OwnershipOutcome.UNGOVERNED
    assert decision.owner_hub_id is None
    assert decision.grants_locally is False
    assert decision.refuses is True


def test_an_assertion_without_configuration_stays_ungoverned() -> None:
    # A peer claiming ownership of a namespace this hub has not governed does not
    # manufacture an owner: with no operator-confirmed owner it still fails closed.
    decision = _ownership().resolve(_NS, asserting_hubs=[_PEER])
    assert decision.outcome is OwnershipOutcome.UNGOVERNED
    assert decision.contesting == ()


def test_a_local_owner_contested_by_a_peer_is_partitioned() -> None:
    decision = _ownership(**{_NS: _LOCAL}).resolve(_NS, asserting_hubs=[_PEER])
    assert decision.outcome is OwnershipOutcome.PARTITIONED
    assert decision.owner_hub_id is None
    assert decision.grants_locally is False
    assert decision.refuses is True
    assert decision.contesting == (_PEER,)


def test_a_remote_owner_contested_by_another_hub_is_partitioned() -> None:
    decision = _ownership(**{_NS: _PEER}).resolve(_NS, asserting_hubs=[_OTHER])
    assert decision.outcome is OwnershipOutcome.PARTITIONED
    assert decision.contesting == (_OTHER,)


def test_the_configured_owner_asserting_is_not_a_partition() -> None:
    decision = _ownership(**{_NS: _PEER}).resolve(_NS, asserting_hubs=[_PEER])
    assert decision.outcome is OwnershipOutcome.REMOTE
    assert decision.owner_hub_id == _PEER


def test_the_resolving_hub_asserting_its_own_namespace_is_not_a_partition() -> None:
    decision = _ownership(**{_NS: _LOCAL}).resolve(_NS, asserting_hubs=[_LOCAL])
    assert decision.outcome is OwnershipOutcome.LOCAL


def test_partition_reports_every_contesting_hub_sorted() -> None:
    decision = _ownership(**{_NS: _LOCAL}).resolve(
        _NS, asserting_hubs=["syn-z", "syn-a", _PEER, _LOCAL]
    )
    assert decision.outcome is OwnershipOutcome.PARTITIONED
    assert decision.contesting == ("syn-a", _PEER, "syn-z")


def test_owner_of_returns_the_configured_owner_or_none() -> None:
    ownership = _ownership(**{_NS: _PEER})
    assert ownership.owner_of(_NS) == _PEER
    assert ownership.owner_of("UNGOVERNED-NS") is None
