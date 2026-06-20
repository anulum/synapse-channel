# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — exhaustive tests for the coordination state registry

from __future__ import annotations

from synapse_channel.state import (
    MINIMUM_TTL_SECONDS,
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
        status="in_progress",
        data_ref="mem://k",
    )
    assert claim.as_dict() == {
        "task_id": "T",
        "owner": "A",
        "note": "n",
        "claimed_at": 1.0,
        "lease_expires_at": 2.0,
        "status": "in_progress",
        "data_ref": "mem://k",
        "worktree": "",
        "paths": [],
        "epoch": 0,
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
        "A", "TASK-8", status="completed", note="  done  ", data_ref="  mem://x  ", now=1010.0
    )
    assert ok is True
    claim = state.claims["TASK-8"]
    assert claim.status == "completed"
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
    ok, _ = state.update_task("A", "T1", status="in_progress", epoch=epoch, now=1010.0)
    assert ok is True
    assert state.claims["T1"].status == "in_progress"


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
