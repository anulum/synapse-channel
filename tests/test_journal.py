# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for durable event recording and replay

from __future__ import annotations

from pathlib import Path

from synapse_channel.core.journal import (
    MEMORY_KINDS,
    EventKind,
    record_chat,
    record_checkpoint,
    record_claim,
    record_finding,
    record_handoff,
    record_idempotency,
    record_ledger_progress,
    record_ledger_task,
    record_release,
    record_resource,
    record_task_update,
    replay,
)
from synapse_channel.core.ledger import LedgerTask, ProgressNote
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state import GitContext, ResourceOffer, TaskClaim


def _store(tmp_path: Path) -> EventStore:
    return EventStore(tmp_path / "events.db")


def _claim(**overrides: object) -> TaskClaim:
    base: dict[str, object] = {
        "task_id": "T1",
        "owner": "A",
        "note": "n",
        "claimed_at": 1000.0,
        "lease_expires_at": 5000.0,
        "status": "claimed",
        "data_ref": "",
        "worktree": "wt",
        "paths": ("src",),
        "epoch": 3,
    }
    base.update(overrides)
    return TaskClaim(**base)  # type: ignore[arg-type]


def test_record_claim_writes_claim_kind(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record_claim(store, _claim())
    events = store.read_all()
    store.close()
    assert events[0].kind == EventKind.CLAIM
    assert events[0].payload["task_id"] == "T1"


def test_replay_reconstructs_claim_scope_and_epoch(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record_claim(store, _claim(epoch=3, worktree="wt", paths=("src", "tests")))
    result = replay(store, now=2000.0)
    store.close()

    claim = result.state.claims["T1"]
    assert claim.owner == "A"
    assert claim.worktree == "wt"
    assert claim.paths == ("src", "tests")
    assert claim.epoch == 3
    assert result.state._epoch_seq == 3
    assert result.state.last_seen["A"] == 1000.0


def test_replay_seeds_custom_per_agent_quotas(tmp_path: Path) -> None:
    store = _store(tmp_path)
    result = replay(store, max_claims_per_agent=7, max_offers_per_agent=3)
    store.close()
    assert result.state.max_claims_per_agent == 7
    assert result.state.max_offers_per_agent == 3


def test_replay_seeds_custom_blackboard_progress_bounds(tmp_path: Path) -> None:
    store = _store(tmp_path)
    result = replay(store, max_progress=9, max_progress_per_author=4, max_progress_per_task=5)
    store.close()
    assert result.blackboard.max_progress == 9
    assert result.blackboard.max_progress_per_author == 4
    assert result.blackboard.max_progress_per_task == 5


def test_replay_seeds_custom_max_paths_per_claim(tmp_path: Path) -> None:
    store = _store(tmp_path)
    result = replay(store, max_paths_per_claim=9)
    store.close()
    assert result.state.max_paths_per_claim == 9


def test_replay_reconstructs_git_context(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ctx = GitContext(branch="feature/x", base="develop", auto_release_on="commit")
    record_claim(store, _claim(git=ctx))
    result = replay(store, now=2000.0)
    store.close()
    assert result.state.claims["T1"].git == ctx


def test_replay_release_removes_claim(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record_claim(store, _claim())
    record_release(store, "T1")
    result = replay(store, now=2000.0)
    store.close()
    assert "T1" not in result.state.claims


def test_record_checkpoint_writes_checkpoint_kind(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record_checkpoint(store, _claim(checkpoint="cursor=9"))
    events = store.read_all()
    store.close()
    # Distinct kind so the read-side can pick out resume summaries.
    assert events[0].kind == EventKind.CHECKPOINT
    assert events[0].payload["checkpoint"] == "cursor=9"


def test_record_handoff_writes_handoff_kind(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record_handoff(store, _claim(owner="B"))
    events = store.read_all()
    store.close()
    assert events[0].kind == EventKind.HANDOFF
    assert events[0].payload["owner"] == "B"


def test_replay_reconstructs_claim_from_checkpoint_kind(tmp_path: Path) -> None:
    # A checkpoint event carries the full claim snapshot, so coordination replay
    # reconstructs the claim — including the durable checkpoint — from it.
    store = _store(tmp_path)
    record_claim(store, _claim(checkpoint=""))
    record_checkpoint(store, _claim(checkpoint="cursor=9", epoch=4))
    result = replay(store, now=2000.0)
    store.close()
    claim = result.state.claims["T1"]
    assert claim.checkpoint == "cursor=9"
    assert claim.epoch == 4


def test_replay_reconstructs_owner_from_handoff_kind(tmp_path: Path) -> None:
    # A handoff event reconstructs ownership exactly as a claim event would.
    store = _store(tmp_path)
    record_claim(store, _claim(owner="A"))
    record_handoff(store, _claim(owner="B"))
    result = replay(store, now=2000.0)
    store.close()
    assert result.state.claims["T1"].owner == "B"


def test_replay_task_update_overwrites_claim(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record_claim(store, _claim(status="claimed", epoch=3))
    record_task_update(store, _claim(status="completed", data_ref="mem://x", epoch=4))
    result = replay(store, now=2000.0)
    store.close()
    claim = result.state.claims["T1"]
    assert claim.status == "completed"
    assert claim.data_ref == "mem://x"
    assert result.state._epoch_seq == 4


def test_replay_reconstructs_resource_offer(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record_resource(
        store,
        ResourceOffer(
            agent="A", kind="llm", name="m", capacity=2, meta={"v": "8G"}, offered_at=1900.0
        ),
    )
    result = replay(store, now=1950.0)
    store.close()
    offer = result.state.resources["A:llm:m"]
    assert offer.capacity == 2
    assert offer.meta == {"v": "8G"}


def test_replay_collects_chat_history_and_message_seq(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record_chat(store, {"type": "chat", "payload": "a", "msg_id": 1})
    record_chat(store, {"type": "chat", "payload": "b", "msg_id": 2})
    result = replay(store, now=2000.0)
    store.close()
    assert [m["payload"] for m in result.chat_history] == ["a", "b"]
    assert result.message_seq == 2


def test_record_ledger_task_replays_blackboard_task(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record_ledger_task(
        store,
        LedgerTask(
            task_id="PLAN",
            title="Plan",
            created_at=1000.0,
            updated_at=1001.0,
            description="do the work",
            depends_on=("READY",),
            status="blocked",
            suggested_owner="A",
            created_by="planner",
        ),
    )
    result = replay(store, now=2000.0)
    store.close()
    task = result.blackboard.tasks["PLAN"]
    assert task.title == "Plan"
    assert task.depends_on == ("READY",)
    assert task.status == "blocked"


def test_record_ledger_progress_replays_blackboard_note(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record_ledger_progress(
        store,
        ProgressNote(
            task_id="PLAN",
            author="A",
            kind="assessment",
            text="checked",
            posted_at=1000.0,
        ),
    )
    result = replay(store, now=2000.0)
    store.close()
    assert [
        (note.task_id, note.author, note.kind, note.text) for note in result.blackboard.progress
    ] == [("PLAN", "A", "assessment", "checked")]


def test_replay_applies_blackboard_progress_retention(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for text in ("a1", "a2", "a3"):
        store.append(
            EventKind.LEDGER_PROGRESS,
            ProgressNote(task_id="T", author="A", kind="note", text=text).as_dict(),
        )
    store.append(
        EventKind.LEDGER_PROGRESS,
        ProgressNote(task_id="T", author="B", kind="note", text="b1").as_dict(),
    )
    result = replay(store, max_progress=10, max_progress_per_author=2, max_progress_per_task=3)
    store.close()
    assert [(note.author, note.text) for note in result.blackboard.progress] == [
        ("A", "a2"),
        ("A", "a3"),
        ("B", "b1"),
    ]


def test_replay_counts_existing_findings_by_actor(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record_finding(store, {"statement": "a", "provenance": {"actor": "A"}})
    record_finding(store, {"statement": "b", "provenance": {"actor": "A"}})
    record_finding(store, {"statement": "c", "provenance": {"actor": "B"}})
    result = replay(store, now=2000.0)
    store.close()
    assert result.finding_counts_by_actor == {"A": 2, "B": 1}


def test_replay_ignores_findings_without_actor(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record_finding(store, {"statement": "a", "provenance": {"actor": ""}})
    record_finding(store, {"statement": "b", "provenance": "legacy"})
    result = replay(store, now=2000.0)
    store.close()
    assert result.finding_counts_by_actor == {}


def test_replay_expires_stale_claim(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record_claim(store, _claim(lease_expires_at=1500.0))
    result = replay(store, now=2000.0)  # lease already lapsed
    store.close()
    assert "T1" not in result.state.claims


def test_replay_skips_unknown_event_kind(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.append("mystery", {"whatever": 1})
    record_claim(store, _claim())
    result = replay(store, now=2000.0)
    store.close()
    assert "T1" in result.state.claims  # known event still applied


def test_memory_kinds_are_the_read_side_ingest_set() -> None:
    # The read-side ingests the query-stream, the atoms, and episodic state — and
    # never the pure coordination kinds (claims/releases/resources/ledger).
    assert MEMORY_KINDS == {
        EventKind.RECALL,
        EventKind.FINDING,
        EventKind.CHECKPOINT,
        EventKind.HANDOFF,
    }
    assert EventKind.CLAIM not in MEMORY_KINDS
    assert EventKind.CHAT not in MEMORY_KINDS


def test_record_idempotency_writes_idempotency_kind(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record_idempotency(store, "k1", {"type": "claim_granted", "task_id": "T1"})
    events = store.read_all()
    store.close()
    assert events[0].kind == EventKind.IDEMPOTENCY
    assert events[0].payload == {
        "key": "k1",
        "response": {"type": "claim_granted", "task_id": "T1"},
    }


def test_replay_reconstructs_idempotency_latest_per_key(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record_idempotency(store, "k1", {"v": 1})
    record_idempotency(store, "k1", {"v": 2})  # re-remembered: latest wins, moves to end
    record_idempotency(store, "k2", {"v": 3})
    result = replay(store, now=2000.0)
    store.close()
    assert result.idempotency == [("k1", {"v": 2}), ("k2", {"v": 3})]


def test_replay_idempotency_skips_a_blank_key(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record_idempotency(store, "", {"v": 1})  # a keyless mutation leaves no guard
    record_idempotency(store, "k1", {"v": 2})
    result = replay(store, now=2000.0)
    store.close()
    assert result.idempotency == [("k1", {"v": 2})]


def test_replay_empty_log_yields_empty_state(tmp_path: Path) -> None:
    store = _store(tmp_path)
    result = replay(store, now=2000.0)
    store.close()
    assert result.state.claims == {}
    assert result.chat_history == []
    assert result.message_seq == 0


def test_replay_up_to_seq_bounds_the_reconstruction(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record_claim(store, _claim(task_id="T1"))  # seq 1
    record_claim(store, _claim(task_id="T2"))  # seq 2

    at1 = replay(store, up_to_seq=1, now=1000.0)
    at2 = replay(store, up_to_seq=2, now=1000.0)
    full = replay(store, now=1000.0)

    assert {c.task_id for c in at1.state.claims.values()} == {"T1"}
    assert {c.task_id for c in at2.state.claims.values()} == {"T1", "T2"}
    assert {c.task_id for c in full.state.claims.values()} == {"T1", "T2"}


def test_replay_up_to_seq_none_is_the_whole_log(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record_claim(store, _claim(task_id="T1"))
    assert (
        replay(store, up_to_seq=None, now=1000.0).state.claims
        == replay(store, now=1000.0).state.claims
    )
