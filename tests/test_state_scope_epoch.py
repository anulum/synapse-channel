# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — exhaustive tests for the coordination state registry

from __future__ import annotations

from synapse_channel.core.state import (
    SynapseState,
)


def test_claim_records_normalized_scope_and_epoch() -> None:
    state = SynapseState(default_ttl_seconds=300)
    ok, _ = state.claim("A", "T1", worktree="wt", paths=["./src/", "src"], now=1000.0)
    assert ok is True
    claim = state.claims["T1"]
    assert claim.worktree == "wt"
    assert claim.paths == ("src",)
    assert claim.epoch == 1


def test_claim_scope_overlap_is_denied() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "T1", paths=["src"], now=1000.0)
    ok, msg = state.claim("B", "T2", paths=["src/app.py"], now=1001.0)
    assert ok is False
    assert "file scope conflicts with 'T1' held by A" in msg


def test_traversal_like_claim_widens_to_whole_worktree() -> None:
    state = SynapseState(default_ttl_seconds=300)
    ok, _ = state.claim("A", "T1", paths=["src/../tests"], now=1000.0)
    assert ok is True
    assert state.claims["T1"].paths == ("",)

    ok, msg = state.claim("B", "T2", paths=["docs"], now=1001.0)
    assert ok is False
    assert "file scope conflicts with 'T1' held by A" in msg


def test_claim_disjoint_scopes_both_succeed() -> None:
    state = SynapseState(default_ttl_seconds=300)
    ok1, _ = state.claim("A", "T1", paths=["src"], now=1000.0)
    ok2, _ = state.claim("B", "T2", paths=["tests"], now=1001.0)
    assert ok1 is True
    assert ok2 is True
    assert state.claims["T2"].epoch == 2


def test_same_agent_overlapping_claims_do_not_self_conflict() -> None:
    state = SynapseState(default_ttl_seconds=300)
    ok1, _ = state.claim("A", "T1", paths=["src"], now=1000.0)
    ok2, _ = state.claim("A", "T2", paths=["src/app.py"], now=1001.0)
    assert ok1 is True
    assert ok2 is True


def test_renewing_own_claim_with_new_scope_does_not_self_conflict() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "T1", paths=["src"], now=1000.0)
    ok, _ = state.claim("A", "T1", paths=["src", "tests"], now=1050.0)
    assert ok is True
    claim = state.claims["T1"]
    assert claim.paths == ("src", "tests")
    assert claim.epoch == 2  # renewal bumps the epoch


def test_different_worktrees_do_not_conflict() -> None:
    state = SynapseState(default_ttl_seconds=300)
    ok1, _ = state.claim("A", "T1", worktree="wt-a", paths=["src"], now=1000.0)
    ok2, _ = state.claim("B", "T2", worktree="wt-b", paths=["src"], now=1001.0)
    assert ok1 is True
    assert ok2 is True


def test_claim_as_dict_includes_scope_and_epoch() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "T1", worktree="wt", paths=["src"], now=1000.0)
    snap = state.claims["T1"].as_dict()
    assert snap["worktree"] == "wt"
    assert snap["paths"] == ["src"]
    assert snap["epoch"] == 1


# --- epoch validation on release / update ------------------------------------


def test_release_with_matching_epoch_succeeds() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "T1", now=1000.0)
    epoch = state.claims["T1"].epoch
    ok, _ = state.release("A", "T1", now=1010.0, epoch=epoch)
    assert ok is True
    assert "T1" not in state.claims


def test_release_with_stale_epoch_is_rejected() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "T1", now=1000.0)
    ok, msg = state.release("A", "T1", now=1010.0, epoch=999)
    assert ok is False
    assert "epoch is stale" in msg
    assert "T1" in state.claims  # not released


def test_update_task_with_matching_epoch_succeeds() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "T1", now=1000.0)
    epoch = state.claims["T1"].epoch
    ok, _ = state.update_task("A", "T1", status="working", epoch=epoch, now=1010.0)
    assert ok is True
    assert state.claims["T1"].status == "working"


def test_update_task_with_stale_epoch_is_rejected() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "T1", now=1000.0)
    ok, msg = state.update_task("A", "T1", status="done", epoch=999, now=1010.0)
    assert ok is False
    assert "epoch is stale" in msg
    assert state.claims["T1"].status == "claimed"


def test_epoch_is_strictly_increasing_across_claims() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "T1", paths=["a"], now=1000.0)
    state.claim("A", "T2", paths=["b"], now=1000.0)
    state.claim("A", "T3", paths=["c"], now=1000.0)
    assert [state.claims[t].epoch for t in ("T1", "T2", "T3")] == [1, 2, 3]
