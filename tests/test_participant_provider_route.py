# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for provider/model routing
"""Tests for :mod:`synapse_channel.participants.provider_route`.

The router is pure over candidate descriptors with an injected ``PATH`` resolver. The suite asserts
the eligibility filters (undrivable, missing tags, at-limit) and the ranking order (headroom, then
cost, then channel robustness), and that an unroutable task selects ``None``.
"""

from __future__ import annotations

from synapse_channel.core.accounting import ModelPrice
from synapse_channel.participants.channel_select import ProviderCapabilities
from synapse_channel.participants.participant import ParticipantChannel
from synapse_channel.participants.provider_route import (
    ModelCandidate,
    TaskProfile,
    select_provider,
)


def _present(name: str) -> str | None:
    return f"/usr/bin/{name}"


def _absent(name: str) -> str | None:
    return None


def _headless(binary: str = "claude") -> ProviderCapabilities:
    return ProviderCapabilities(headless_binary=binary)


def test_returns_none_when_no_candidate_is_drivable() -> None:
    # A headless-only candidate whose binary does not resolve is not drivable.
    candidate = ModelCandidate(name="c", model="m", capabilities=_headless("ghost"))
    assert select_provider(TaskProfile(), [candidate], which=_absent) is None


def test_returns_none_when_no_candidate_has_required_tags() -> None:
    candidate = ModelCandidate(
        name="c", model="m", capabilities=_headless(), tags=frozenset({"text"})
    )
    task = TaskProfile(required_tags=frozenset({"vision"}))
    assert select_provider(task, [candidate], which=_present) is None


def test_at_or_over_limit_candidate_is_dropped() -> None:
    maxed = ModelCandidate(
        name="maxed", model="m", capabilities=_headless(), rate_limit_utilisation=1.0
    )
    assert select_provider(TaskProfile(), [maxed], which=_present) is None


def test_picks_the_only_eligible_candidate() -> None:
    candidate = ModelCandidate(name="solo", model="m", capabilities=_headless())
    choice = select_provider(TaskProfile(), [candidate], which=_present)
    assert choice is not None
    assert choice.candidate.name == "solo"
    assert choice.channel is ParticipantChannel.HEADLESS
    assert choice.estimated_cost == 0.0
    assert "solo" in choice.reason


def test_more_headroom_wins_over_less() -> None:
    busy = ModelCandidate(
        name="busy", model="m", capabilities=_headless(), rate_limit_utilisation=0.9
    )
    fresh = ModelCandidate(
        name="fresh", model="m", capabilities=_headless(), rate_limit_utilisation=0.1
    )
    choice = select_provider(TaskProfile(), [busy, fresh], which=_present)
    assert choice is not None
    assert choice.candidate.name == "fresh"


def test_cheaper_wins_when_headroom_is_equal() -> None:
    pricey = ModelCandidate(
        name="pricey",
        model="m",
        capabilities=_headless(),
        price=ModelPrice(input_per_1k=10.0, output_per_1k=10.0),
    )
    cheap = ModelCandidate(
        name="cheap",
        model="m",
        capabilities=_headless(),
        price=ModelPrice(input_per_1k=1.0, output_per_1k=1.0),
    )
    task = TaskProfile(estimated_input_tokens=1000, estimated_output_tokens=1000)
    choice = select_provider(task, [pricey, cheap], which=_present)
    assert choice is not None
    assert choice.candidate.name == "cheap"
    assert choice.estimated_cost == 2.0


def test_more_robust_channel_breaks_a_cost_and_headroom_tie() -> None:
    # Same (free) cost and full headroom; the MCP candidate beats the headless one.
    mcp = ModelCandidate(
        name="mcp", model="m", capabilities=ProviderCapabilities(mcp_reachable=True)
    )
    headless = ModelCandidate(name="headless", model="m", capabilities=_headless())
    choice = select_provider(TaskProfile(), [headless, mcp], which=_present)
    assert choice is not None
    assert choice.candidate.name == "mcp"
    assert choice.channel is ParticipantChannel.MCP


def test_required_tags_subset_is_satisfied() -> None:
    candidate = ModelCandidate(
        name="coder",
        model="m",
        capabilities=_headless(),
        tags=frozenset({"code", "long-context"}),
    )
    task = TaskProfile(required_tags=frozenset({"code"}))
    choice = select_provider(task, [candidate], which=_present)
    assert choice is not None
    assert choice.candidate.name == "coder"


def test_local_unpriced_candidate_ranks_as_free() -> None:
    local = ModelCandidate(
        name="local", model="gemma3:1b", capabilities=ProviderCapabilities(api_reachable=True)
    )
    priced = ModelCandidate(
        name="cloud",
        model="m",
        capabilities=_headless(),
        price=ModelPrice(input_per_1k=5.0, output_per_1k=5.0),
    )
    task = TaskProfile(estimated_input_tokens=1000, estimated_output_tokens=1000)
    choice = select_provider(task, [priced, local], which=_present)
    assert choice is not None
    # The free local model wins on cost, and is reached over the API channel.
    assert choice.candidate.name == "local"
    assert choice.channel is ParticipantChannel.API
