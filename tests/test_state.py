# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — exhaustive tests for the coordination state registry

from __future__ import annotations

from synapse_channel.core.state import (
    MINIMUM_TTL_SECONDS,
    GitContext,
    ResourceOffer,
    SynapseState,
    TaskClaim,
)


def test_default_ttl_is_clamped_to_minimum() -> None:
    state = SynapseState(default_ttl_seconds=1.0)
    assert state.default_ttl_seconds == MINIMUM_TTL_SECONDS


def test_taskclaim_as_dict_exposes_all_public_fields() -> None:
    claim = TaskClaim(
        task_id="T",
        owner="A",
        note="n",
        claimed_at=1.0,
        lease_expires_at=2.0,
        status="working",
        data_ref="mem://k",
        version=4,
        checkpoint="step-3",
    )
    assert claim.as_dict() == {
        "task_id": "T",
        "owner": "A",
        "note": "n",
        "claimed_at": 1.0,
        "lease_expires_at": 2.0,
        "status": "working",
        "data_ref": "mem://k",
        "worktree": "",
        "paths": [],
        "epoch": 0,
        "version": 4,
        "checkpoint": "step-3",
        "git": None,
    }


def test_resourceoffer_defaults_are_independent() -> None:
    first = ResourceOffer(agent="A", kind="llm", name="m1")
    second = ResourceOffer(agent="B", kind="llm", name="m2")
    first.meta["x"] = 1
    assert second.meta == {}
    assert first.capacity == 1


# --- claim -------------------------------------------------------------------


def test_claim_requires_non_empty_task_id() -> None:
    state = SynapseState()
    ok, msg = state.claim("A", "   ")
    assert ok is False
    assert "required" in msg


def test_claim_strips_task_and_note() -> None:
    state = SynapseState(default_ttl_seconds=300)
    ok, _ = state.claim("A", "  TASK-1  ", note="  do work  ", now=1000.0)
    assert ok is True
    claim = state.claims["TASK-1"]
    assert claim.owner == "A"
    assert claim.note == "do work"
    assert claim.lease_expires_at == 1000.0 + 300.0


def test_claim_blocked_by_live_owner() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "TASK-2", now=1000.0)
    ok, msg = state.claim("B", "TASK-2", now=1010.0)
    assert ok is False
    assert "already claimed by A" in msg


def test_owner_renews_extends_lease() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "TASK-3", now=1000.0)
    first = state.claims["TASK-3"].lease_expires_at
    ok, _ = state.claim("A", "TASK-3", now=1050.0)
    assert ok is True
    assert state.claims["TASK-3"].lease_expires_at > first


def test_expired_claim_can_be_taken_over() -> None:
    state = SynapseState(default_ttl_seconds=60)
    state.claim("A", "TASK-4", now=1000.0)
    ok, _ = state.claim("B", "TASK-4", now=1070.0)
    assert ok is True
    assert state.claims["TASK-4"].owner == "B"


def test_explicit_ttl_is_clamped_to_minimum() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "TASK-5", ttl_seconds=1.0, now=1000.0)
    assert state.claims["TASK-5"].lease_expires_at == 1000.0 + MINIMUM_TTL_SECONDS


def test_explicit_ttl_above_minimum_is_honoured() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "TASK-6", ttl_seconds=120.0, now=1000.0)
    assert state.claims["TASK-6"].lease_expires_at == 1000.0 + 120.0


# --- update_task -------------------------------------------------------------


def test_update_task_unknown_returns_error() -> None:
    state = SynapseState()
    ok, msg = state.update_task("A", "NOPE")
    assert ok is False
    assert "not found" in msg


def test_update_task_rejects_non_owner() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "TASK-7", now=1000.0)
    ok, msg = state.update_task("B", "TASK-7", status="blocked", now=1010.0)
    assert ok is False
    assert "owned by A" in msg


def test_update_task_sets_fields() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "TASK-8", now=1000.0)
    ok, _ = state.update_task(
        "A", "TASK-8", status="done", note="  done  ", data_ref="  mem://x  ", now=1010.0
    )
    assert ok is True
    claim = state.claims["TASK-8"]
    assert claim.status == "done"
    assert claim.note == "done"
    assert claim.data_ref == "mem://x"


def test_update_task_ignores_empty_status() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "TASK-9", now=1000.0)
    state.update_task("A", "TASK-9", status="", now=1010.0)
    assert state.claims["TASK-9"].status == "claimed"


# --- release -----------------------------------------------------------------


def test_release_requires_task_id() -> None:
    state = SynapseState()
    ok, msg = state.release("A", "  ")
    assert ok is False
    assert "required" in msg


def test_release_unclaimed_returns_error() -> None:
    state = SynapseState()
    ok, msg = state.release("A", "GHOST")
    assert ok is False
    assert "not currently claimed" in msg


def test_release_rejects_non_owner() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "TASK-10", now=1000.0)
    ok, msg = state.release("B", "TASK-10", now=1010.0)
    assert ok is False
    assert "owned by A" in msg


def test_release_roundtrip() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.claim("A", "TASK-11", now=1000.0)
    ok, msg = state.release("A", "TASK-11", now=1010.0)
    assert ok is True
    assert "released" in msg
    assert "TASK-11" not in state.claims


# --- resources ---------------------------------------------------------------


def test_offer_resource_returns_key_and_clamps_capacity() -> None:
    state = SynapseState()
    key = state.offer_resource("A", kind="llm", name="m", capacity=0, now=1000.0)
    assert key == "A:llm:m"
    assert state.resources[key].capacity == 1
    assert state.resources[key].meta == {}


def test_offer_resource_keeps_meta_and_refreshes() -> None:
    state = SynapseState()
    state.offer_resource("A", kind="llm", name="m", meta={"vram": "8G"}, now=1000.0)
    state.offer_resource("A", kind="llm", name="m", meta={"vram": "16G"}, now=1100.0)
    offer = state.resources["A:llm:m"]
    assert offer.meta == {"vram": "16G"}
    assert offer.offered_at == 1100.0


def test_query_resources_filters_and_sorts() -> None:
    state = SynapseState()
    state.offer_resource("B", kind="llm", name="z", now=1000.0)
    state.offer_resource("A", kind="compute", name="gpu", now=1000.0)
    state.offer_resource("A", kind="llm", name="a", now=1000.0)

    everything = state.query_resources()
    assert [(r["agent"], r["kind"], r["name"]) for r in everything] == [
        ("A", "compute", "gpu"),
        ("A", "llm", "a"),
        ("B", "llm", "z"),
    ]

    only_llm = state.query_resources(kind="llm")
    assert {r["name"] for r in only_llm} == {"a", "z"}


def test_resource_offer_expires_after_ttl() -> None:
    state = SynapseState()
    state.offer_resource("A", kind="llm", name="m", now=1000.0)
    # A heartbeat far in the future triggers the soft-TTL sweep.
    state.heartbeat("A", now=1000.0 + 301.0)
    assert state.resources == {}


# --- snapshot ----------------------------------------------------------------


def test_snapshot_reports_claims_agents_and_resources() -> None:
    state = SynapseState(default_ttl_seconds=300)
    state.heartbeat("A", now=1000.0)
    state.heartbeat("B", now=1000.0)
    state.claim("A", "TASK-12", note="sync", now=1000.0)
    state.offer_resource("B", kind="llm", name="m", now=1000.0)

    snap = state.snapshot(now=1001.0)
    assert snap["generated_at"] == 1001.0
    assert len(snap["active_claims"]) == 1
    assert snap["active_claims"][0]["task_id"] == "TASK-12"
    assert {item["agent"] for item in snap["agents"]} == {"A", "B"}
    assert snap["resources"][0]["name"] == "m"


def test_snapshot_drops_expired_claim() -> None:
    state = SynapseState(default_ttl_seconds=60)
    state.claim("A", "TASK-13", now=1000.0)
    snap = state.snapshot(now=1000.0 + 61.0)
    assert snap["active_claims"] == []


# --- file-scoped claims + overlap --------------------------------------------


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


# --- typed lifecycle + optimistic-concurrency (CAS) --------------------------


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


def test_gitcontext_as_dict_round_trips() -> None:
    ctx = GitContext(branch="feature/x", base="develop", auto_release_on="commit")
    assert ctx.as_dict() == {
        "branch": "feature/x",
        "base": "develop",
        "auto_release_on": "commit",
    }
    assert GitContext.from_dict(ctx.as_dict()) == ctx


def test_gitcontext_defaults() -> None:
    ctx = GitContext(branch="main")
    assert ctx.base == "main"
    assert ctx.auto_release_on == "merge"


def test_gitcontext_from_dict_normalises_unknown_mode_and_empty_base() -> None:
    ctx = GitContext.from_dict({"branch": "wip", "base": "", "auto_release_on": "nonsense"})
    assert ctx.branch == "wip"
    assert ctx.base == "main"  # empty base falls back
    assert ctx.auto_release_on == "manual"  # unknown trigger falls back


def test_gitcontext_from_dict_uses_field_defaults() -> None:
    ctx = GitContext.from_dict({"branch": "wip"})
    assert ctx == GitContext(branch="wip", base="main", auto_release_on="merge")


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
