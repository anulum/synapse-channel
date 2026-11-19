# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — routing ranks local candidates first; metered use needs a reference
"""A declared local candidate ranks first, and hosted metered use is never automatic.

These tests pin the rules T121-1 to T121-4 of the C121 unit contract.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from synapse_channel.core.accounting import ModelPrice
from synapse_channel.participants.channel_select import ProviderCapabilities
from synapse_channel.participants.provider_route import (
    ModelCandidate,
    RoutingDecision,
    TaskProfile,
    route_candidates,
)
from synapse_channel.participants.provider_route_policy import EvidenceStatus, PriceKind

NOW = 100.0


def _candidate(name: str, **updates: Any) -> ModelCandidate:
    base = ModelCandidate(
        name=name, model="m", capabilities=ProviderCapabilities(api_reachable=True)
    )
    return replace(base, **updates)


def _current_quota(name: str, **updates: Any) -> ModelCandidate:
    quota: dict[str, Any] = {
        "rate_limit_utilisation": 0.1,
        "quota_source": "operator",
        "quota_observed_at": 90.0,
        "quota_valid_until": 110.0,
    }
    return _candidate(name, **(quota | updates))


def _priced(name: str, **updates: Any) -> ModelCandidate:
    return _current_quota(
        name,
        price=ModelPrice(input_per_1k=1.0, output_per_1k=1.0),
        price_observed_at=90.0,
        price_valid_until=110.0,
        **updates,
    )


def _route(task: TaskProfile, *candidates: ModelCandidate) -> RoutingDecision:
    return route_candidates(task, list(candidates), now=NOW)


def _chosen(decision: RoutingDecision) -> str:
    assert decision.choice is not None
    return decision.choice.candidate.name


# --- T121-1: locality is the first ranking key ---------------------------------------------


def test_a_free_local_candidate_with_unknown_quota_beats_a_hosted_one_with_current_quota() -> None:
    local = _candidate("local", price_kind=PriceKind.FREE, locality="local")
    hosted = _current_quota("hosted", price_kind=PriceKind.FREE)
    decision = _route(TaskProfile(), hosted, local)

    assert _chosen(decision) == "local"
    assert decision.choice is not None
    assert decision.choice.quota_status is EvidenceStatus.UNKNOWN
    assert "in the local tier" in decision.choice.reason
    # Without the locality key the hosted candidate's current quota would win.
    assert _chosen(_route(TaskProfile(), hosted, replace(local, locality="hosted"))) == "hosted"


# --- T121-2: the order inside each tier is today's ------------------------------------------


def test_the_order_inside_each_tier_is_unchanged() -> None:
    busy = _current_quota("busy", price_kind=PriceKind.FREE, rate_limit_utilisation=0.8)
    idle = _current_quota("idle", price_kind=PriceKind.FREE, rate_limit_utilisation=0.2)
    for locality in ("local", "hosted"):
        decision = _route(
            TaskProfile(),
            replace(busy, locality=locality),
            replace(idle, locality=locality),
        )
        assert _chosen(decision) == "idle"
        assert decision.choice is not None
        assert f"in the {locality} tier" in decision.choice.reason


# --- T121-3: hosted metered use needs an explicit reference ----------------------------------


def test_a_hosted_priced_candidate_needs_a_metered_reference() -> None:
    hosted = _priced("hosted")
    refused = _route(TaskProfile(), hosted)
    assert refused.choice is None
    assert [(item.candidate, item.code) for item in refused.rejected] == [
        ("hosted", "metered_without_reference")
    ]

    chosen = _route(TaskProfile(metered_ref="run-7"), hosted)
    assert _chosen(chosen) == "hosted"
    assert chosen.rejected == ()


def test_a_local_priced_candidate_needs_no_metered_reference() -> None:
    assert _chosen(_route(TaskProfile(), _priced("local", locality="local"))) == "local"


def test_a_metered_reference_must_not_be_blank() -> None:
    with pytest.raises(ValueError, match="metered_ref must be empty or a reference"):
        TaskProfile(metered_ref=" ")


# --- T121-4: an undeclared candidate is hosted ----------------------------------------------


def test_a_candidate_without_a_declared_locality_ranks_as_hosted() -> None:
    undeclared = _current_quota("undeclared", price_kind=PriceKind.FREE)
    assert undeclared.locality == "hosted"
    local = _candidate("local", price_kind=PriceKind.FREE, locality="local")
    assert _chosen(_route(TaskProfile(), undeclared, local)) == "local"
    assert _route(TaskProfile(), _priced("undeclared")).choice is None


def test_an_unknown_locality_is_refused() -> None:
    with pytest.raises(ValueError, match="locality must be 'local' or 'hosted'"):
        _candidate("odd", locality="edge")
