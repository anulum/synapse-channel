# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — exhaustive tests for the coordination state registry

from __future__ import annotations

from synapse_channel.core.scoping import MAX_DECLARED_PATHS
from synapse_channel.core.state import (
    LEASE_HEAP_COMPACT_FLOOR,
    MAX_CLAIMS_PER_AGENT,
    MAX_OFFERS_PER_AGENT,
    SynapseState,
)


def test_claim_cap_refuses_new_claims_past_the_bound() -> None:
    state = SynapseState(default_ttl_seconds=10_000.0)
    for index in range(MAX_CLAIMS_PER_AGENT):
        ok, _ = state.claim("A", f"T{index}", now=0.0, worktree=f"wt{index}")
        assert ok
    refused, message = state.claim("A", "OVERFLOW", now=0.0, worktree="wtX")
    assert refused is False
    assert "maximum" in message
    # Renewing an already-held claim is free, and another agent has its own budget.
    assert state.claim("A", "T0", now=1.0, worktree="wt0")[0] is True
    assert state.claim("B", "B-TASK", now=0.0, worktree="wtB")[0] is True


def test_offer_cap_refuses_new_offers_past_the_bound() -> None:
    state = SynapseState()
    for index in range(MAX_OFFERS_PER_AGENT):
        assert state.offer_resource("A", kind="llm", name=f"m{index}", now=0.0) is not None
    assert state.offer_resource("A", kind="llm", name="overflow", now=0.0) is None
    # Refreshing an existing offer is allowed; a different agent has its own budget.
    assert state.offer_resource("A", kind="llm", name="m0", now=1.0) is not None
    assert state.offer_resource("B", kind="llm", name="m0", now=0.0) is not None


def test_claim_cap_honours_a_custom_limit() -> None:
    state = SynapseState(default_ttl_seconds=10_000.0, max_claims_per_agent=2)
    assert state.claim("A", "T0", now=0.0, worktree="wt0")[0] is True
    assert state.claim("A", "T1", now=0.0, worktree="wt1")[0] is True
    refused, message = state.claim("A", "T2", now=0.0, worktree="wt2")
    assert refused is False
    assert "maximum 2 claims" in message


def test_offer_cap_honours_a_custom_limit() -> None:
    state = SynapseState(max_offers_per_agent=2)
    assert state.offer_resource("A", kind="llm", name="m0", now=0.0) is not None
    assert state.offer_resource("A", kind="llm", name="m1", now=0.0) is not None
    assert state.offer_resource("A", kind="llm", name="m2", now=0.0) is None


def test_quota_limits_clamp_up_to_one() -> None:
    state = SynapseState(max_claims_per_agent=0, max_offers_per_agent=-5)
    assert state.max_claims_per_agent == 1
    assert state.max_offers_per_agent == 1
    assert state.claim("A", "T0", now=0.0, worktree="wt0")[0] is True
    assert state.claim("A", "T1", now=0.0, worktree="wt1")[0] is False
    assert state.offer_resource("A", kind="llm", name="m0", now=0.0) is not None
    assert state.offer_resource("A", kind="llm", name="m1", now=0.0) is None


def test_quota_limits_default_to_module_constants() -> None:
    state = SynapseState()
    assert state.max_claims_per_agent == MAX_CLAIMS_PER_AGENT
    assert state.max_offers_per_agent == MAX_OFFERS_PER_AGENT
    assert state.max_paths_per_claim == MAX_DECLARED_PATHS


def test_max_paths_per_claim_widens_an_oversized_claim_to_the_worktree() -> None:
    # A claim declaring more distinct paths than the cap owns the whole worktree, so
    # another agent's disjoint claim in that worktree is then refused (conservative).
    state = SynapseState(max_paths_per_claim=2)
    granted, _ = state.claim("A", "T0", now=0.0, worktree="wt", paths=["a/f", "b/f", "c/f"])
    assert granted is True
    assert state.claims["T0"].paths == ("",)
    refused, message = state.claim("B", "T1", now=0.0, worktree="wt", paths=["z/f"])
    assert refused is False
    assert "file scope conflicts" in message


def test_max_paths_per_claim_keeps_a_claim_within_the_cap_scoped() -> None:
    # Within the cap the declared paths are kept, so a disjoint claim is granted.
    state = SynapseState(max_paths_per_claim=2)
    assert state.claim("A", "T0", now=0.0, worktree="wt", paths=["a/f", "b/f"])[0] is True
    assert state.claims["T0"].paths == ("a/f", "b/f")
    assert state.claim("B", "T1", now=0.0, worktree="wt", paths=["z/f"])[0] is True


def test_max_paths_per_claim_clamps_up_to_one() -> None:
    state = SynapseState(max_paths_per_claim=0)
    assert state.max_paths_per_claim == 1
    granted, _ = state.claim("A", "T0", now=0.0, worktree="wt", paths=["a/f", "b/f"])
    assert granted is True
    assert state.claims["T0"].paths == ("",)


# --- lease-expiry heap -------------------------------------------------------


def test_renewed_lease_survives_its_superseded_heap_entry() -> None:
    # A renewal leaves the previous heap entry behind; when that stale entry comes
    # due the live claim (a newer epoch) must not be expired by it.
    state = SynapseState(default_ttl_seconds=100.0)
    state.claim("A", "T1", ttl_seconds=100.0, now=0.0)  # epoch 1, expires 100
    state.claim("A", "T1", ttl_seconds=100.0, now=50.0)  # epoch 2, expires 150
    # At t=120 the stale (100, T1, epoch1) entry is due but the live lease is not.
    state.heartbeat("A", now=120.0)
    assert "T1" in state.claims
    assert state.claims["T1"].epoch == 2


def test_released_task_leaves_only_a_harmless_stale_heap_entry() -> None:
    # Release deletes the claim but not its heap entry; popping the orphan later
    # is a no-op, not a crash.
    state = SynapseState(default_ttl_seconds=100.0)
    state.claim("A", "T1", now=0.0)
    state.release("A", "T1", now=10.0)
    state.heartbeat("A", now=10_000.0)  # the orphaned (100, T1, 1) entry is skipped
    assert "T1" not in state.claims


def test_lease_heap_stays_bounded_under_renewal_churn() -> None:
    # Renewing one task many times must not grow the heap without limit: the
    # churn-bound rebuild keeps it proportional to the live claim count.
    state = SynapseState(default_ttl_seconds=100.0)
    for tick in range(40):
        state.claim("A", "T1", ttl_seconds=100.0, now=float(tick))
    assert len(state.claims) == 1
    assert len(state._lease_heap) <= 2 * len(state.claims) + LEASE_HEAP_COMPACT_FLOOR
    # Expiry is still exact after the compaction.
    state.heartbeat("A", now=10_000.0)
    assert state.claims == {}


def test_reindex_leases_rebuilds_the_heap_from_live_claims() -> None:
    state = SynapseState(default_ttl_seconds=100.0)
    # Distinct worktrees so the two whole-worktree claims do not contend.
    state.claim("A", "T1", now=0.0, worktree="wtA")
    state.heartbeat("B", now=0.0)
    state.claim("B", "T2", now=0.0, worktree="wtB")
    # Poison the heap with a ghost entry, then rebuild from the registry.
    state._lease_heap = [(999.0, "ghost", 999)]
    state.reindex_leases()
    assert sorted(entry[1] for entry in state._lease_heap) == ["T1", "T2"]


def test_many_distinct_claims_all_expire_together() -> None:
    # The heap must expire every lapsed lease, not just the heap root.
    state = SynapseState(default_ttl_seconds=100.0)
    for index in range(30):
        state.heartbeat(f"A{index}", now=0.0)
        # Each in its own worktree so whole-worktree scopes never conflict.
        state.claim(f"A{index}", f"T{index}", ttl_seconds=100.0, now=0.0, worktree=f"wt{index}")
    assert len(state.claims) == 30
    state.heartbeat("A0", now=200.0)  # every lease has lapsed by now
    assert state.claims == {}
