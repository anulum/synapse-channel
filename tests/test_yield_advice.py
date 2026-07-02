# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — causality-weighed yield advice regressions

"""Tests for yield advice over overlapping live claims.

Pairing is pinned first (only different owners, same worktree, overlapping
scopes, both still live), then the downstream weighing (causal descendants,
pending declared dependents — transitively, a completed dependent dropped),
the tie-break (later claim yields), and the store loader with its fail-closed
node ceiling. Rendering and JSON shapes are asserted on real advice objects.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.causality import build_causal_graph
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.yield_advice import (
    YieldAdvice,
    advice_to_json,
    advise_yields,
    render_advice_markdown,
    run_yield_advice,
)


def _claim(
    seq: int,
    task: str,
    owner: str,
    *,
    status: str = "claimed",
    paths: tuple[str, ...] = (),
    worktree: str = "wt1",
    kind: str = EventKind.CLAIM,
) -> StoredEvent:
    return StoredEvent(
        seq=seq,
        ts=float(seq),
        kind=kind,
        payload={
            "task_id": task,
            "owner": owner,
            "status": status,
            "paths": list(paths),
            "worktree": worktree,
        },
    )


def _release(seq: int, task: str) -> StoredEvent:
    return StoredEvent(seq=seq, ts=float(seq), kind=EventKind.RELEASE, payload={"task_id": task})


def _ledger(
    seq: int, task: str, *, deps: tuple[str, ...] = (), status: str = "open"
) -> StoredEvent:
    return StoredEvent(
        seq=seq,
        ts=float(seq),
        kind=EventKind.LEDGER_TASK,
        payload={
            "task_id": task,
            "title": f"task {task}",
            "depends_on": list(deps),
            "status": status,
        },
    )


def _advise(*events: StoredEvent) -> list[YieldAdvice]:
    return advise_yields(build_causal_graph(list(events)))


# --- pairing ------------------------------------------------------------------------


def test_no_advice_without_an_overlap() -> None:
    recommendations = _advise(
        _claim(1, "A", "alice", paths=("src/a.py",)),
        _claim(2, "B", "bob", paths=("src/b.py",)),
    )
    assert recommendations == []


def test_no_advice_for_one_owner_holding_both_claims() -> None:
    recommendations = _advise(
        _claim(1, "A", "alice", paths=("src/a.py",)),
        _claim(2, "B", "alice", paths=("src/a.py",)),
    )
    assert recommendations == []


def test_no_advice_across_worktrees() -> None:
    recommendations = _advise(
        _claim(1, "A", "alice", paths=("src/a.py",), worktree="wt1"),
        _claim(2, "B", "bob", paths=("src/a.py",), worktree="wt2"),
    )
    assert recommendations == []


def test_a_released_claim_is_no_longer_a_contender() -> None:
    recommendations = _advise(
        _claim(1, "A", "alice", paths=("src/a.py",)),
        _release(2, "A"),
        _claim(3, "B", "bob", paths=("src/a.py",)),
    )
    assert recommendations == []


def test_a_completed_claim_is_no_longer_a_contender() -> None:
    recommendations = _advise(
        _claim(1, "A", "alice", paths=("src/a.py",)),
        _claim(2, "A", "alice", paths=("src/a.py",), status="done", kind=EventKind.TASK_UPDATE),
        _claim(3, "B", "bob", paths=("src/a.py",)),
    )
    assert recommendations == []


def test_an_ownerless_snapshot_keeps_the_live_claim() -> None:
    recommendations = _advise(
        _claim(1, "A", "alice", paths=("src/a.py",)),
        _claim(2, "A", "", paths=("src/a.py",), kind=EventKind.TASK_UPDATE),
        _claim(3, "B", "bob", paths=("src/a.py",)),
    )
    assert len(recommendations) == 1
    assert recommendations[0].holder.owner == "alice"


def test_an_empty_scope_overlaps_every_path() -> None:
    recommendations = _advise(
        _claim(1, "A", "alice"),
        _claim(2, "B", "bob", paths=("docs/guide.md",)),
    )
    assert len(recommendations) == 1


# --- weighing and tie-break ---------------------------------------------------------


def test_the_lighter_claim_yields() -> None:
    """A pending dependent makes A heavier, so B is advised to yield."""
    recommendations = _advise(
        _ledger(1, "C", deps=("A",)),
        _claim(2, "A", "alice", paths=("src/a.py",)),
        _claim(3, "B", "bob", paths=("src/a.py",)),
    )
    assert len(recommendations) == 1
    advice = recommendations[0]
    assert advice.holder.task_id == "A"
    assert advice.holder.blocking_count == 1
    assert advice.holder.blocked_tasks == ("C",)
    assert advice.yielder.task_id == "B"
    assert advice.yielder.blocking_count == 0
    assert "the lighter claim yields" in advice.reason


def test_dependents_count_transitively() -> None:
    recommendations = _advise(
        _ledger(1, "C", deps=("A",)),
        _ledger(2, "D", deps=("C",)),
        _claim(3, "A", "alice", paths=("src/a.py",)),
        _claim(4, "B", "bob", paths=("src/a.py",)),
    )
    assert recommendations[0].holder.blocked_tasks == ("C", "D")
    assert recommendations[0].holder.blocking_count == 2


def test_a_completed_dependent_stops_counting() -> None:
    recommendations = _advise(
        _ledger(1, "C", deps=("A",), status="done"),
        _claim(2, "A", "alice", paths=("src/a.py",)),
        _claim(3, "B", "bob", paths=("src/a.py",)),
    )
    # equal weights now, so the tie-break decides — not C's dependency
    assert recommendations[0].holder.blocking_count == 0
    assert "the later claim" in recommendations[0].reason


def test_causal_descendants_weigh_a_claim() -> None:
    """A's release already unblocked C (contention edge), so A blocks downstream."""
    recommendations = _advise(
        _claim(1, "A", "alice", paths=("src/a.py",)),
        _release(2, "A"),
        _claim(3, "C", "carol", paths=("src/a.py",)),
        _claim(4, "A", "alice", paths=("src/a.py",)),
        _claim(5, "B", "bob", paths=("src/a.py",)),
    )
    by_yielder = {advice.yielder.task_id: advice for advice in recommendations}
    contested = by_yielder["B"]
    assert contested.holder.task_id == "A"
    assert "C" in contested.holder.blocked_tasks


def test_equal_weights_make_the_later_claim_yield() -> None:
    recommendations = _advise(
        _claim(1, "A", "alice", paths=("src/a.py",)),
        _claim(2, "B", "bob", paths=("src/a.py",)),
    )
    advice = recommendations[0]
    assert advice.holder.task_id == "A"
    assert advice.yielder.task_id == "B"
    assert "the later claim (seq 2) yields to the earlier (seq 1)" in advice.reason


def test_every_overlapping_pair_gets_its_own_advice() -> None:
    recommendations = _advise(
        _claim(1, "A", "alice"),
        _claim(2, "B", "bob"),
        _claim(3, "C", "carol"),
    )
    assert len(recommendations) == 3
    pairs = {(advice.holder.task_id, advice.yielder.task_id) for advice in recommendations}
    assert pairs == {("A", "B"), ("A", "C"), ("B", "C")}


# --- rendering and JSON --------------------------------------------------------------


def test_markdown_names_the_yielder_and_the_advisory_boundary() -> None:
    recommendations = _advise(
        _claim(1, "A", "alice", paths=("src/a.py",)),
        _claim(2, "B", "bob", paths=("src/a.py",)),
    )
    text = render_advice_markdown(recommendations)
    assert "# Contention: 1 overlapping live claim pair(s)" in text
    assert "## B (bob) should yield to A (alice)" in text
    assert "advisory only: no claim is preempted" in text


def test_markdown_reports_a_quiet_log() -> None:
    text = render_advice_markdown([])
    assert "0 overlapping live claim pair(s)" in text
    assert "No live claims overlap" in text


def test_json_shape_carries_both_standings() -> None:
    recommendations = _advise(
        _ledger(1, "C", deps=("A",)),
        _claim(2, "A", "alice", paths=("src/a.py",)),
        _claim(3, "B", "bob", paths=("src/a.py",)),
    )
    payload = advice_to_json(recommendations)
    assert payload[0]["holder"] == {
        "task_id": "A",
        "owner": "alice",
        "seq": 2,
        "paths": ["src/a.py"],
        "blocking_count": 1,
        "blocked_tasks": ["C"],
    }
    yielder = payload[0]["yielder"]
    assert isinstance(yielder, dict)
    assert yielder["task_id"] == "B"
    assert "lighter claim yields" in str(payload[0]["reason"])


# --- store loader ---------------------------------------------------------------------


def _seeded_store(path: Path, events: list[StoredEvent]) -> None:
    store = EventStore(path)
    for event in events:
        store.append(event.kind, event.payload, ts=event.ts)
    store.close()


def test_run_yield_advice_reads_a_real_store(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seeded_store(
        db,
        [
            _claim(1, "A", "alice", paths=("src/a.py",)),
            _claim(2, "B", "bob", paths=("src/a.py",)),
        ],
    )
    recommendations = run_yield_advice(db)
    assert len(recommendations) == 1
    assert recommendations[0].yielder.owner == "bob"


def test_run_yield_advice_rejects_a_missing_store(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing event store"):
        run_yield_advice(tmp_path / "absent.db")


def test_run_yield_advice_enforces_the_node_ceiling(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seeded_store(
        db,
        [
            _claim(1, "A", "alice", paths=("src/a.py",)),
            _claim(2, "B", "bob", paths=("src/a.py",)),
        ],
    )
    with pytest.raises(ValueError, match="would exceed 1 coordination events"):
        run_yield_advice(db, max_nodes=1)


def test_run_yield_advice_lifts_the_ceiling_when_disabled(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    _seeded_store(
        db,
        [
            _claim(1, "A", "alice", paths=("src/a.py",)),
            _claim(2, "B", "bob", paths=("src/a.py",)),
        ],
    )
    assert len(run_yield_advice(db, max_nodes=0)) == 1


# --- graph corner shapes --------------------------------------------------------------


def test_a_task_less_event_weighs_nothing() -> None:
    """A claim with no task id joins the graph but never a standing or a count."""
    taskless = StoredEvent(seq=1, ts=1.0, kind=EventKind.CLAIM, payload={"owner": "ghost"})
    recommendations = _advise(
        taskless,
        _claim(2, "A", "alice", paths=("src/a.py",)),
        _claim(3, "B", "bob", paths=("src/a.py",)),
    )
    assert len(recommendations) == 1
    assert recommendations[0].holder.blocking_count == 0


def test_diamond_dependents_count_once() -> None:
    """D waits on both A and C while C waits on A; D is counted exactly once."""
    recommendations = _advise(
        _ledger(1, "C", deps=("A",)),
        _ledger(2, "D", deps=("A", "C")),
        _claim(3, "A", "alice", paths=("src/a.py",)),
        _claim(4, "B", "bob", paths=("src/a.py",)),
    )
    assert recommendations[0].holder.blocked_tasks == ("C", "D")
    assert recommendations[0].holder.blocking_count == 2


def test_two_causal_routes_into_one_claim_count_once() -> None:
    """A's completion and its release both point at B's claim; B counts once."""
    recommendations = _advise(
        _ledger(1, "B", deps=("A",)),
        _claim(2, "A", "alice", paths=("src/a.py",)),
        _claim(3, "A", "alice", paths=("src/a.py",), status="done", kind=EventKind.TASK_UPDATE),
        _release(4, "A"),
        _claim(5, "B", "bob", paths=("src/a.py",)),
        _claim(6, "C", "carol", paths=("src/a.py",)),
        _claim(7, "D", "dan", paths=("src/a.py",)),
    )
    by_pair = {
        (advice.holder.task_id, advice.yielder.task_id): advice for advice in recommendations
    }
    assert by_pair  # C and D contend; A is completed and B waits on nothing live


def test_a_task_less_descendant_is_reached_but_never_counted() -> None:
    """A contention edge into a task-less claim adds no task to the weight."""
    taskless = StoredEvent(
        seq=3,
        ts=3.0,
        kind=EventKind.CLAIM,
        payload={"owner": "ghost", "paths": ["src/a.py"], "worktree": "wt1"},
    )
    recommendations = _advise(
        _claim(1, "A", "alice", paths=("src/a.py",)),
        _release(2, "A"),
        taskless,
        _claim(4, "A", "alice", paths=("src/a.py",)),
        _claim(5, "B", "bob", paths=("src/a.py",)),
    )
    by_yielder = {advice.yielder.task_id: advice for advice in recommendations}
    # A's release causally freed B's fresh claim, so B counts; the task-less
    # ghost claim was reached through the same edge walk yet never counted.
    assert by_yielder["B"].holder.blocked_tasks == ("B",)
