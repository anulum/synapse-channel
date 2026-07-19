# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — exhaustive tests for the coordination state registry

from __future__ import annotations

from synapse_channel.core.state import (
    GitContext,
    SynapseState,
)


def test_legal_transition_bumps_version() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "T1", now=1000.0)
    assert state.claims["T1"].version == 0
    ok, _ = state.update_task("A", "T1", status="working", now=1010.0)
    assert ok is True
    assert state.claims["T1"].status == "working"
    assert state.claims["T1"].version == 1


def test_illegal_transition_is_rejected() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "T1", now=1000.0)  # status "claimed"
    ok, msg = state.update_task("A", "T1", status="input_required", now=1010.0)
    assert ok is False
    assert "cannot transition claimed -> input_required" in msg
    assert state.claims["T1"].status == "claimed"
    assert state.claims["T1"].version == 0  # nothing applied


def test_version_match_succeeds_and_bumps() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "T1", now=1000.0)
    ok, _ = state.update_task("A", "T1", status="working", expected_version=0, now=1010.0)
    assert ok is True
    assert state.claims["T1"].version == 1


def test_stale_version_is_rejected() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "T1", now=1000.0)
    state.update_task("A", "T1", status="working", now=1010.0)  # version -> 1
    ok, msg = state.update_task("A", "T1", note="late", expected_version=0, now=1020.0)
    assert ok is False
    assert "version conflict" in msg
    assert state.claims["T1"].note == ""  # not applied


def test_reclaim_resets_version() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "T1", now=1000.0)
    state.update_task("A", "T1", status="working", now=1010.0)  # version -> 1
    state.claim("A", "T1", now=1050.0)  # renew -> fresh claim
    assert state.claims["T1"].version == 0
    assert state.claims["T1"].status == "claimed"


# --- atomic handoff ----------------------------------------------------------


def test_handoff_transfers_ownership_and_preserves_context() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "T1", note="parser work", worktree="wt", paths=["src"], now=1000.0)
    state.update_task("A", "T1", status="working", data_ref="mem://k", now=1005.0)
    old_epoch = state.claims["T1"].epoch

    ok, message = state.handoff("A", "T1", "B", now=1010.0)
    assert ok and "handed from A to B" in message
    claim = state.claims["T1"]
    assert claim.owner == "B"
    assert claim.status == "working"  # work continues, not reset
    assert claim.data_ref == "mem://k"
    assert claim.note == "parser work"  # kept when no replacement given
    assert claim.worktree == "wt" and claim.paths == ("src",)  # scope preserved
    assert claim.epoch > old_epoch  # fresh epoch invalidates the old owner
    assert claim.version == 0  # fresh CAS baseline for the new owner
    assert claim.lease_expires_at == 1010.0 + 300


def test_handoff_can_replace_the_note() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "T1", note="old", now=1000.0)
    state.handoff("A", "T1", "B", note="  picking up from A  ", now=1010.0)
    assert state.claims["T1"].note == "picking up from A"


def test_handoff_requires_id_and_target() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "T1", now=1000.0)
    assert state.handoff("A", "  ", "B")[0] is False
    ok, reason = state.handoff("A", "T1", "   ")
    assert not ok and "target is required" in reason


def test_handoff_rejects_unclaimed_and_non_owner_and_self() -> None:
    state = SynapseState(default_ttl_seconds=300)
    ok, reason = state.handoff("A", "GHOST", "B", now=1000.0)
    assert not ok and "not currently claimed" in reason

    state.claim("A", "T1", now=1000.0)
    ok, reason = state.handoff("C", "T1", "B", now=1001.0)
    assert not ok and "owned by A" in reason

    ok, reason = state.handoff("A", "T1", "A", now=1002.0)
    assert not ok and "already owned by A" in reason


def test_handoff_rejects_stale_epoch() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "T1", now=1000.0)
    ok, reason = state.handoff("A", "T1", "B", epoch=999, now=1010.0)
    assert not ok and "epoch is stale" in reason
    assert state.claims["T1"].owner == "A"  # unchanged


def test_handoff_refuses_when_recipient_is_at_claim_cap() -> None:
    """BUG-7: handoff must not grow a recipient past max_claims_per_agent."""
    state = SynapseState(default_ttl_seconds=10_000.0, max_claims_per_agent=2)
    assert state.claim("A", "SOURCE", now=0.0, worktree="wtA")[0]
    assert state.claim("B", "B0", now=0.0, worktree="wtB0")[0]
    assert state.claim("B", "B1", now=0.0, worktree="wtB1")[0]
    ok, reason = state.handoff("A", "SOURCE", "B", now=1.0)
    assert ok is False
    assert "maximum 2 claims" in reason
    assert state.claims["SOURCE"].owner == "A"
    assert state.claims["B0"].owner == "B"
    assert state.claims["B1"].owner == "B"


def test_handoff_succeeds_when_recipient_has_remaining_claim_budget() -> None:
    """A recipient under the cap still receives the task atomically."""
    state = SynapseState(default_ttl_seconds=10_000.0, max_claims_per_agent=2)
    assert state.claim("A", "SOURCE", now=0.0, worktree="wtA")[0]
    assert state.claim("B", "B0", now=0.0, worktree="wtB0")[0]
    ok, message = state.handoff("A", "SOURCE", "B", now=1.0)
    assert ok is True
    assert "handed from A to B" in message
    assert state.claims["SOURCE"].owner == "B"
    assert "SOURCE" in state.claims and "B0" in state.claims


def test_handoff_preserves_checkpoint() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "T1", now=1000.0)
    state.save_checkpoint("A", "T1", "cp", now=1010.0)
    state.handoff("A", "T1", "B", now=1020.0)
    assert state.claims["T1"].checkpoint == "cp"


# --- resumable checkpoints ---------------------------------------------------


def test_save_checkpoint_stores_and_bumps_version() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "T1", now=1000.0)
    ok, message = state.save_checkpoint("A", "  T1 ", "step-2", now=1005.0)
    assert ok and "Checkpoint saved" in message
    assert state.claims["T1"].checkpoint == "step-2"
    assert state.claims["T1"].version == 1


def test_save_checkpoint_rejects_unknown_non_owner_and_stale_epoch() -> None:
    state = SynapseState(default_ttl_seconds=300)
    assert state.save_checkpoint("A", "GHOST", "x", now=1000.0)[0] is False
    state.claim("A", "T1", now=1000.0)
    ok, reason = state.save_checkpoint("B", "T1", "x", now=1001.0)
    assert not ok and "owned by A" in reason
    ok, reason = state.save_checkpoint("A", "T1", "x", epoch=999, now=1002.0)
    assert not ok and "epoch is stale" in reason


def test_checkpoint_survives_expiry_and_resumes_on_takeover() -> None:
    state = SynapseState(default_ttl_seconds=60)
    state.claim("A", "T1", now=1000.0)
    state.save_checkpoint("A", "T1", "cursor=42", now=1010.0)
    # The 60s lease lapses; a heartbeat past it expires the claim and retains cp.
    state.heartbeat("Z", now=1100.0)
    assert "T1" not in state.claims
    assert state.expired_checkpoints["T1"] == "cursor=42"
    # B takes over and resumes from the retained checkpoint, which is consumed.
    ok, _ = state.claim("B", "T1", now=1110.0)
    assert ok
    assert state.claims["T1"].owner == "B"
    assert state.claims["T1"].checkpoint == "cursor=42"
    assert "T1" not in state.expired_checkpoints


def test_renewal_preserves_own_checkpoint() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "T1", now=1000.0)
    state.save_checkpoint("A", "T1", "cp", now=1010.0)
    state.claim("A", "T1", now=1020.0)  # renew while still live
    assert state.claims["T1"].checkpoint == "cp"  # preserved across renewal
    assert state.claims["T1"].version == 0  # but the version still resets


def test_release_clears_retained_checkpoint() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "T1", now=1000.0)
    state.expired_checkpoints["T1"] = "stale"  # a leftover retained token
    state.release("A", "T1", now=1010.0)
    assert "T1" not in state.expired_checkpoints


def test_claim_stores_and_exposes_git_context() -> None:
    state = SynapseState(default_ttl_seconds=300)
    ctx = GitContext(branch="feature/y", base="main", auto_release_on="merge")
    ok, _ = state.claim("A", "T1", now=1000.0, git=ctx)
    assert ok
    assert state.claims["T1"].git == ctx
    assert state.claims["T1"].as_dict()["git"] == ctx.as_dict()


def test_handoff_carries_git_context() -> None:
    state = SynapseState(default_ttl_seconds=300)
    ctx = GitContext(branch="feature/z")
    state.claim("A", "T1", now=1000.0, git=ctx)
    state.heartbeat("B", now=1000.0)
    ok, _ = state.handoff("A", "T1", "B", now=1001.0)
    assert ok
    assert state.claims["T1"].git == ctx


# --- per-agent quotas --------------------------------------------------------
