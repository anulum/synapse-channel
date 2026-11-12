# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — historical assignment index contracts
"""Exercise native board and claim lifecycle maintenance of recipient indexes."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from synapse_channel.core.assignment_origin import AssignmentOrigin
from synapse_channel.core.ledger import Blackboard, LedgerTask
from synapse_channel.core.pending_index import PendingRecipientIndex, assignment_key, scoped_owner
from synapse_channel.core.pending_work import PendingItem, PendingQuery, read_pending_work
from synapse_channel.core.state import SynapseState, TaskClaim


def task(task_id: str = "ALPHA/old", **changes: Any) -> LedgerTask:
    values: dict[str, Any] = dict(
        task_id=task_id,
        title="private task",
        created_at=1,
        updated_at=1,
        project="ALPHA",
        suggested_owner="ALPHA/worker",
        created_by="ALPHA/planner",
    )
    return LedgerTask(**(values | changes))


def claim(**changes: Any) -> TaskClaim:
    values: dict[str, Any] = dict(
        task_id="ALPHA/handoff",
        owner="ALPHA/worker",
        note="private",
        claimed_at=1,
        lease_expires_at=1000,
        epoch=7,
    )
    return TaskClaim(**(values | changes))


def test_recipient_move_retain_remove_and_capacity_are_local() -> None:
    index = PendingRecipientIndex()
    index.remove("missing")
    index.set("task", "alice", "a" * 64)
    index.set("task", "alice", "a" * 64)
    assert index.rows("alice", limit=1) == (("task", "a" * 64),)
    index.retain("task", "alice", "a" * 64)
    index.set("task", "bob", "b" * 64)
    assert not index.rows("alice", limit=1)
    with pytest.raises(ValueError, match="capacity"):
        index.rows("bob", limit=0)
    index.retain("task", "bob", "c" * 64)
    assert not index.entries and not index.recipients
    assert assignment_key("tasks", "task", "alice", 1) != assignment_key("tasks", "task", "bob", 1)


@pytest.mark.parametrize(
    ("kind", "key"),
    [("claims", "a" * 64), ("tasks", "ALPHA/raw-task-id"), ("tasks", "A" * 64), ("handoffs", 7)],
)
def test_a_pending_item_holds_only_a_known_family_and_a_fingerprint(kind: Any, key: Any) -> None:
    """A raw task id, an unknown family or a non-string key never becomes a pending item."""
    with pytest.raises(ValueError, match="invalid pending work item"):
        PendingItem(kind, key)
    assert PendingItem("handoffs", "b" * 64).encode() == {"kind": "handoffs", "key": "b" * 64}


@pytest.mark.parametrize(
    "fields", [(None, 1, True), ("a", True, True), ("a", 0, True), ("a", 2**63, True), ("a", 1, 1)]
)
def test_invalid_origin_fields_refuse(fields: tuple[Any, ...]) -> None:
    with pytest.raises(ValueError, match="assignment origin"):
        AssignmentOrigin(*fields)


@pytest.mark.parametrize(
    "value",
    [
        [],
        {},
        {"version": True, "owner": "a", "anchor": 1, "legacy": True},
        {"version": 2, "owner": "a", "anchor": 1, "legacy": True},
    ],
)
def test_origin_shape_refuses(value: Any) -> None:
    with pytest.raises(ValueError, match="assignment origin"):
        AssignmentOrigin.from_value(value)


def test_origin_roundtrip_and_project_qualification() -> None:
    origin = AssignmentOrigin("ALPHA/worker", 2, True)
    assert AssignmentOrigin.from_value(origin.encode()) == origin
    assert AssignmentOrigin.from_value(None) is None
    assert scoped_owner("ALPHA/task", "ALPHA/worker", "ALPHA")
    for owner, project, task_id in [
        ("worker", "ALPHA", "ALPHA/t"),
        ("ALPHA/worker", "", "ALPHA/t"),
        ("ALPHA/worker", "ALPHA", "BETA/t"),
        (" ALPHA/worker", " ALPHA", " ALPHA/t"),
        ("ALPHA/", "ALPHA", "ALPHA/t"),
    ]:
        assert not scoped_owner(task_id, owner, project)


def test_board_preserves_historical_origin_across_status_and_atomic_publication() -> None:
    board = Blackboard()
    board.restore_task(task())
    key = board.pending_assignments.rows("ALPHA/worker", limit=10)[0][1]
    board.restore_task(task(version=2, status="in_progress"))
    assert board.pending_assignments.rows("ALPHA/worker", limit=10)[0][1] == key
    candidate = deepcopy(board)
    assert candidate.update_task("ALPHA/old", status="blocked")[0]
    board.publish_from(candidate)
    assert board.pending_assignments.rows("ALPHA/worker", limit=10)[0][1] == key
    assert board.post_task(
        task_id="ALPHA/old",
        title="new title",
        author="ALPHA/planner",
        suggested_owner="ALPHA/worker",
        project="ALPHA",
    )[0]
    assert board.pending_assignments.rows("ALPHA/worker", limit=10)[0][1] == key
    assert board.update_task("ALPHA/old", status="done")[0]
    assert not board.pending_assignments.rows("ALPHA/worker", limit=10)
    assert board.update_task("ALPHA/old", status="open")[0]
    assert not board.pending_assignments.rows("ALPHA/worker", limit=10)


def test_current_reassignments_and_known_self_tasks_do_not_become_legacy() -> None:
    board = Blackboard()
    board.restore_task(task(created_by="ALPHA/worker"))
    assert not board.pending_assignments.entries
    board.restore_task(
        task("ALPHA/known", suggested_owner="ALPHA/worker"), notification_recorded=True
    )
    assert not board.pending_assignments.entries
    board.restore_task(task("ALPHA/unknown"))
    assert board.update_task("ALPHA/unknown", suggested_owner="ALPHA/other")[0]
    assert not board.pending_assignments.entries
    board.restore_task(task("ALPHA/redeclare"))
    assert board.post_task(
        task_id="ALPHA/redeclare",
        title="new",
        author="ALPHA/worker",
        suggested_owner="ALPHA/other",
        project="ALPHA",
    )[0]
    assert not board.pending_assignments.entries
    assert board.post_task(
        task_id="ALPHA/current",
        title="new",
        author="ALPHA/planner",
        suggested_owner="ALPHA/worker",
        project="ALPHA",
    )[0]
    assert not board.pending_assignments.entries
    assert "assignment_origin" not in board.tasks["ALPHA/current"].as_dict()


@pytest.mark.parametrize(
    "origin", [AssignmentOrigin("ALPHA/other", 1, True), AssignmentOrigin("ALPHA/worker", 2, True)]
)
def test_wrong_origin_cannot_install_a_task(origin: AssignmentOrigin) -> None:
    board = Blackboard()
    with pytest.raises(ValueError, match="does not match"):
        board.restore_task(task(assignment_origin=origin))
    assert not board.tasks and not board.pending_assignments.entries


@pytest.mark.parametrize("finish", ["release", "force", "expire", "done", "new-owner", "new-scope"])
def test_historical_handoff_index_tracks_real_claim_lifecycle(finish: str) -> None:
    state = SynapseState()
    state.restore_claim(claim(), legacy_handoff=True)
    state.reindex_leases()
    key = state.pending_handoffs.rows("ALPHA/worker", limit=10)[0][1]
    assert state.claim("ALPHA/worker", "ALPHA/handoff", now=10)[0]
    assert state.pending_handoffs.rows("ALPHA/worker", limit=10)[0][1] == key
    moved = deepcopy(state)
    state.publish_from(moved)
    if finish == "release":
        assert state.release("ALPHA/worker", "ALPHA/handoff", now=11)[0]
    elif finish == "force":
        assert state.force_release("ALPHA/handoff", by="operator")[0]
    elif finish == "expire":
        state.heartbeat("ALPHA/worker", now=10000)
    elif finish == "done":
        assert state.update_task("ALPHA/worker", "ALPHA/handoff", status="working", now=11)[0]
        assert state.update_task("ALPHA/worker", "ALPHA/handoff", status="done", now=12)[0]
    elif finish == "new-owner":
        assert state.handoff("ALPHA/worker", "ALPHA/handoff", "ALPHA/other", now=11)[0]
    else:
        assert state.claim("ALPHA/worker", "ALPHA/handoff", worktree="new", now=11)[0]
    assert not state.pending_handoffs.entries


def test_no_inferred_claim_handoff_and_replayed_release_cleanup() -> None:
    state = SynapseState()
    state.restore_claim(claim())
    assert not state.pending_handoffs.entries
    state.restore_claim(claim(), legacy_handoff=True)
    state.restore_claim(claim(status="done"))
    assert not state.pending_handoffs.entries
    state.restore_claim(claim(), legacy_handoff=True)
    state.restore_release("ALPHA/handoff")
    assert not state.claims and not state.pending_handoffs.entries


def test_projection_reads_only_own_recipient_under_large_foreign_population() -> None:
    board, state = Blackboard(), SynapseState()
    for index in range(5000):
        board.restore_task(task(f"BETA/{index}", project="BETA", suggested_owner="BETA/other"))
    board.restore_task(task())
    state.restore_claim(claim(), legacy_handoff=True)
    snapshot = read_pending_work(
        board, state, PendingQuery("ALPHA/worker", ""), hub_id="hub", now=10
    )
    items = snapshot["items"]
    assert snapshot["available"] is True and isinstance(items, list) and len(items) == 2
    assert (
        "private" not in str(snapshot)
        and "ALPHA/old" not in str(snapshot)
        and "BETA" not in str(snapshot)
    )
    expired = read_pending_work(
        board, state, PendingQuery("ALPHA/worker", "hub"), hub_id="hub", now=2000
    )
    remaining = expired["items"]
    assert isinstance(remaining, list) and len(remaining) == 1
    with pytest.raises(ValueError, match="capacity"):
        read_pending_work(
            board, state, PendingQuery("ALPHA/worker", "hub", 1), hub_id="hub", now=10
        )
    with pytest.raises(ValueError, match="hub changed"):
        read_pending_work(
            board, state, PendingQuery("ALPHA/worker", "foreign"), hub_id="hub", now=10
        )


def test_completing_one_old_task_preserves_the_other_recipient_assignment() -> None:
    board = Blackboard()
    board.restore_task(task("ALPHA/first"))
    board.restore_task(task("ALPHA/second"))
    assert board.update_task("ALPHA/first", status="done")[0]
    remaining = board.pending_assignments.rows("ALPHA/worker", limit=2)
    assert [task_id for task_id, _ in remaining] == ["ALPHA/second"]


def test_explicit_notification_coverage_supersedes_unknown_historical_origin() -> None:
    board = Blackboard()
    board.restore_task(task())
    assert board.pending_assignments.entries
    board.restore_task(task(version=2), notification_recorded=True)
    assert not board.pending_assignments.entries
