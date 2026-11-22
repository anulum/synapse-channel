# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the coordination state's public port: epoch, expiry, publication

from __future__ import annotations

import inspect
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields

import pytest

from synapse_channel.core.state import (
    DEFAULT_RESOURCE_TTL_SECONDS,
    StatePublicationParts,
    SynapseState,
)
from synapse_channel.core.state_leases import LeaseIndex


def test_epoch_seq_is_the_last_issued_lease_epoch() -> None:
    state = SynapseState(default_ttl_seconds=100.0)
    assert state.epoch_seq == 0

    assert state.claim("A", "T1", now=0.0, worktree="wtA")[0] is True
    assert state.claims["T1"].epoch == state.epoch_seq == 1

    assert state.claim("B", "T1", now=1.0, worktree="wtA")[0] is False
    assert state.epoch_seq == 1

    assert state.claim("A", "T2", now=2.0, worktree="wtB")[0] is True
    assert state.claims["T2"].epoch == state.epoch_seq == 2


def test_epoch_seq_is_a_property_without_a_setter() -> None:
    member = inspect.getattr_static(SynapseState, "epoch_seq")

    assert isinstance(member, property)
    assert member.fset is None


def test_publication_parts_are_the_states_own_registries_in_a_frozen_record() -> None:
    state = SynapseState()

    parts = state.publication_parts()

    assert isinstance(parts, StatePublicationParts)
    assert parts.resource_registry.resources is state.resources
    assert parts.lease_index is state._lease_index
    assert [field.name for field in fields(parts)] == ["resource_registry", "lease_index"]
    for field in fields(parts):
        with pytest.raises(FrozenInstanceError):
            setattr(parts, field.name, LeaseIndex())


def test_publish_from_adopts_the_candidates_registries_and_epoch() -> None:
    live = SynapseState(default_ttl_seconds=100.0)
    candidate = deepcopy(live)
    assert candidate.claim("A", "T1", now=0.0, worktree="wtA")[0] is True
    key = candidate.offer_resource("A", kind="llm", name="model", now=0.0)
    parts = candidate.publication_parts()

    live.publish_from(candidate)

    published = live.publication_parts()
    assert published.resource_registry is parts.resource_registry
    assert published.lease_index is parts.lease_index
    assert live.resources is parts.resource_registry.resources
    assert key in live.resources
    assert live.epoch_seq == candidate.epoch_seq == 1

    live.expire_stale(100.0)
    assert "T1" not in live.claims


def test_publish_from_refused_for_its_configuration_changes_nothing() -> None:
    live = SynapseState(default_ttl_seconds=100.0)
    assert live.claim("A", "T1", now=0.0, worktree="wtA")[0] is True
    before = live.publication_parts()
    candidate = SynapseState(default_ttl_seconds=200.0)
    assert candidate.claim("B", "T2", now=0.0, worktree="wtB")[0] is True
    assert candidate.claim("B", "T3", now=0.0, worktree="wtC")[0] is True

    with pytest.raises(ValueError, match="configuration does not match"):
        live.publish_from(candidate)

    after = live.publication_parts()
    assert after.resource_registry is before.resource_registry
    assert after.lease_index is before.lease_index
    assert live.epoch_seq == 1
    assert set(live.claims) == {"T1"}


def test_expire_stale_expires_claims_before_offers(monkeypatch: pytest.MonkeyPatch) -> None:
    state = SynapseState()
    calls: list[tuple[str, float]] = []
    monkeypatch.setattr(state, "_expire_claims", lambda now: calls.append(("claims", now)))
    monkeypatch.setattr(state, "_expire_resources", lambda now: calls.append(("offers", now)))

    state.expire_stale(5.0)

    assert calls == [("claims", 5.0), ("offers", 5.0)]


def test_expire_stale_drops_lapsed_claims_and_offers_past_the_default_ttl() -> None:
    state = SynapseState(default_ttl_seconds=100.0)
    assert state.claim("A", "T1", now=0.0, worktree="wtA")[0] is True
    key = state.offer_resource("A", kind="llm", name="model", now=0.0)

    state.expire_stale(99.0)
    assert "T1" in state.claims

    state.expire_stale(100.0)
    assert "T1" not in state.claims
    assert key in state.resources

    state.expire_stale(DEFAULT_RESOURCE_TTL_SECONDS)
    assert key in state.resources

    state.expire_stale(DEFAULT_RESOURCE_TTL_SECONDS + 1.0)
    assert key not in state.resources


def test_heartbeat_and_snapshot_run_the_one_expiry_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    state = SynapseState()
    passes: list[float] = []
    monkeypatch.setattr(state, "expire_stale", passes.append)

    state.heartbeat("A", now=7.0)
    view = state.snapshot(now=8.0)

    assert passes == [7.0, 8.0]
    assert state.last_seen == {"A": 7.0}
    assert view["generated_at"] == 8.0
