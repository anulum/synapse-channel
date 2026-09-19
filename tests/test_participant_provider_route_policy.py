# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — public routing evidence and uncertainty policy tests
"""Exercise operator policy through the exported participant router."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pytest

from synapse_channel.core.accounting import ModelPrice
from synapse_channel.participants.channel_select import ProviderCapabilities
from synapse_channel.participants.provider_route import (
    ModelCandidate,
    RoutingDecision,
    TaskProfile,
    route_candidates,
)
from synapse_channel.participants.provider_route_policy import (
    EvidenceStatus,
    PriceKind,
    RoutingPolicy,
    UnknownHandling,
)


def _candidate(name: str, **updates: Any) -> ModelCandidate:
    base = ModelCandidate(
        name=name,
        model="m",
        capabilities=ProviderCapabilities(api_reachable=True),
        data_classes=frozenset({"public", "private"}),
    )
    return replace(base, **updates)


def _route(task: TaskProfile, *candidates: ModelCandidate) -> RoutingDecision:
    return route_candidates(task, list(candidates), now=100.0)


def test_bounded_routing_admits_explicit_free_local_and_current_priced_remote() -> None:
    local = _candidate("local", price_kind=PriceKind.FREE, rate_limit_utilisation=0.0)
    remote = _candidate(
        "remote",
        price=ModelPrice(input_per_1k=1.0, output_per_1k=2.0),
        price_currency="USD",
        price_revision="2026-09",
        price_source="operator-quote",
        price_observed_at=90.0,
        price_valid_until=110.0,
        rate_limit_utilisation=0.0,
        quota_source="operator",
        quota_observed_at=90.0,
        quota_valid_until=110.0,
    )
    task = TaskProfile(
        estimated_input_tokens=1000, estimated_output_tokens=1000, max_estimated_cost=3.0
    )
    decision = _route(task, remote, local)
    assert decision.choice is not None
    assert decision.choice.candidate.name == "local"
    assert decision.choice.estimated_cost == 0.0
    assert decision.choice.price_status is EvidenceStatus.CURRENT
    remote_only = _route(task, remote)
    assert remote_only.choice is not None
    assert remote_only.choice.estimated_cost == 3.0
    assert remote_only.choice.candidate.price_revision == "2026-09"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({}, "cost_unverified"),
        ({"price_kind": PriceKind.PRICED}, "invalid_price"),
        ({"price_kind": PriceKind.FREE, "price_valid_until": 99.0}, "cost_unverified"),
        ({"price": ModelPrice(2.0, 2.0)}, "cost_ceiling"),
        ({"price": ModelPrice(0.1, 0.1), "price_currency": "EUR"}, "currency_mismatch"),
        ({"account_status": "suspended"}, "account_suspended"),
        ({"rate_limit_utilisation": 1.0}, "quota_exhausted"),
    ],
)
def test_bounded_dispatch_rejects_unverifiable_or_ineligible_routes(
    changes: dict[str, object], code: str
) -> None:
    decision = _route(
        TaskProfile(estimated_input_tokens=1000, max_estimated_cost=1.0),
        _candidate("remote", **changes),
    )
    assert decision.choice is None
    assert [(item.candidate, item.code) for item in decision.rejected] == [("remote", code)]


def test_unknown_and_stale_observations_follow_explicit_operator_policy() -> None:
    unknown = _candidate("unknown")
    stale = _candidate(
        "stale",
        price_kind=PriceKind.FREE,
        price_valid_until=99.0,
        rate_limit_utilisation=0.1,
        quota_valid_until=99.0,
    )
    current = _candidate(
        "current", price_kind=PriceKind.FREE, rate_limit_utilisation=0.2, quota_valid_until=110.0
    )
    decision = _route(TaskProfile(), unknown, stale, current)
    assert decision.choice is not None
    assert decision.choice.candidate.name == "current"
    strict = RoutingPolicy(
        unknown_price="refuse", unknown_quota="refuse", stale_price="refuse", stale_quota="refuse"
    )
    decision = _route(TaskProfile(policy=strict), unknown, stale)
    assert decision.choice is None
    assert [(item.candidate, item.code) for item in decision.rejected] == [
        ("unknown", "unknown_price"),
        ("stale", "stale_price"),
    ]
    quota_only = _route(TaskProfile(policy=RoutingPolicy(stale_quota="refuse")), stale)
    assert quota_only.rejected[0].code == "stale_quota"


def test_data_policy_and_capability_still_gate_a_free_local_route() -> None:
    local = _candidate("local", price_kind=PriceKind.FREE, data_classes=frozenset({"public"}))
    task = TaskProfile(data_classification="private", required_tags=frozenset({"vision"}))
    decision = _route(task, local)
    assert decision.choice is None
    assert decision.rejected[0].code == "missing_capability"
    decision = _route(TaskProfile(data_classification="private"), local)
    assert decision.rejected[0].code == "data_policy"


def test_invalid_and_expired_evidence_never_becomes_a_free_or_fresh_route() -> None:
    invalid = _candidate("invalid", price=ModelPrice(float("nan"), 0.0))
    future = _candidate("future", price_kind=PriceKind.FREE, price_observed_at=101.0)
    decision = _route(TaskProfile(), invalid, future)
    assert decision.choice is None
    assert [item.code for item in decision.rejected] == ["invalid_price", "invalid_price"]


def test_invalid_operator_policy_is_rejected_at_configuration_time() -> None:
    with pytest.raises(ValueError, match="allow.*refuse"):
        RoutingPolicy(unknown_price=cast(UnknownHandling, "ignore"))


@pytest.mark.parametrize(
    "task_input",
    [
        {"estimated_input_tokens": -1},
        {"estimated_output_tokens": -1},
        {"max_estimated_cost": float("nan")},
        {"max_estimated_cost": -1.0},
        {"currency": ""},
        {"data_classification": ""},
    ],
)
def test_invalid_task_request_is_refused_before_dispatch(task_input: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        TaskProfile(**task_input)


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"price_kind": PriceKind.FREE, "price": ModelPrice(0.0, 0.0)}, "invalid_price"),
        ({"rate_limit_utilisation": float("nan")}, "invalid_quota"),
        ({"quota_observed_at": 101.0, "rate_limit_utilisation": 0.0}, "invalid_quota"),
        ({"price_kind": PriceKind.FREE, "price_observed_at": -1.0}, "invalid_price"),
        (
            {"price_kind": PriceKind.FREE, "price_observed_at": 90.0, "price_valid_until": 80.0},
            "invalid_price",
        ),
        ({"price": ModelPrice(1e308, 1e308)}, "invalid_price"),
    ],
)
def test_invalid_provider_evidence_is_not_admitted(changes: dict[str, Any], code: str) -> None:
    decision = _route(
        TaskProfile(estimated_input_tokens=10**300), _candidate("provider", **changes)
    )
    assert decision.choice is None
    assert decision.rejected[0].code == code


def test_unknown_quota_policy_excludes_a_candidate_with_no_observation() -> None:
    task = TaskProfile(policy=RoutingPolicy(unknown_quota="refuse"))
    decision = _route(task, _candidate("free", price_kind=PriceKind.FREE))
    assert decision.choice is None
    assert decision.rejected[0].code == "unknown_quota"


def test_invalid_clock_rejected_even_for_empty_roster() -> None:
    with pytest.raises(ValueError, match="now"):
        route_candidates(TaskProfile(), [], now=float("nan"))


def test_token_estimate_overflow_does_not_dispatch_an_unbounded_route() -> None:
    task = TaskProfile(estimated_input_tokens=10**1000)
    decision = _route(task, _candidate("remote", price=ModelPrice(1.0, 1.0)))
    assert decision.choice is None
    assert decision.rejected[0].code == "invalid_price"
