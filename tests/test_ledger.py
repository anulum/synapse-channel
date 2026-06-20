# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the shared blackboard (task + progress ledgers)

from __future__ import annotations

from synapse_channel.ledger import Blackboard, LedgerTask, ProgressNote

# --- LedgerTask / ProgressNote -----------------------------------------------


def test_ledger_task_as_dict_roundtrips_fields() -> None:
    task = LedgerTask(
        task_id="T1",
        title="Parser",
        created_at=1.0,
        updated_at=2.0,
        description="split tokenizer",
        depends_on=("T0",),
        status="in_progress",
        suggested_owner="FAST",
        created_by="PLANNER",
    )
    assert task.as_dict() == {
        "task_id": "T1",
        "title": "Parser",
        "description": "split tokenizer",
        "depends_on": ["T0"],
        "status": "in_progress",
        "suggested_owner": "FAST",
        "created_by": "PLANNER",
        "created_at": 1.0,
        "updated_at": 2.0,
    }


def test_progress_note_as_dict() -> None:
    note = ProgressNote(task_id="T1", author="FAST", kind="note", text="started", posted_at=3.0)
    assert note.as_dict() == {
        "task_id": "T1",
        "author": "FAST",
        "kind": "note",
        "text": "started",
        "posted_at": 3.0,
    }


# --- post_task ---------------------------------------------------------------


def test_post_task_creates_with_defaults() -> None:
    board = Blackboard()
    ok, message = board.post_task(task_id="  T1 ", title=" Parser ", author="PLANNER", now=10.0)
    assert ok
    assert "declared by PLANNER" in message
    task = board.tasks["T1"]
    assert task.title == "Parser"
    assert task.status == "open"
    assert task.created_by == "PLANNER"
    assert task.created_at == 10.0 == task.updated_at


def test_post_task_requires_id_and_title() -> None:
    board = Blackboard()
    ok, message = board.post_task(task_id="  ", title="x", author="P")
    assert not ok and "Task ID is required" in message
    ok, message = board.post_task(task_id="T1", title="   ", author="P")
    assert not ok and "title is required" in message


def test_post_task_upsert_updates_but_keeps_provenance() -> None:
    board = Blackboard()
    board.post_task(task_id="T1", title="old", author="A", now=1.0)
    ok, message = board.post_task(
        task_id="T1", title="new", author="B", description="d", now=5.0
    )
    assert ok and "re-declared by B" in message
    task = board.tasks["T1"]
    assert task.title == "new"
    assert task.description == "d"
    assert task.created_by == "A"  # provenance preserved
    assert task.created_at == 1.0
    assert task.updated_at == 5.0


def test_post_task_cleans_dependencies() -> None:
    board = Blackboard()
    board.post_task(
        task_id="T1", title="t", author="A", depends_on=[" T2 ", "T2", "", "T1", "T3"]
    )
    # Stripped, de-duplicated, self-reference and blanks removed.
    assert board.tasks["T1"].depends_on == ("T2", "T3")


def test_post_task_rejects_dependency_cycle() -> None:
    board = Blackboard()
    board.post_task(task_id="A", title="a", author="P")
    board.post_task(task_id="B", title="b", author="P", depends_on=["A"])
    ok, message = board.post_task(task_id="A", title="a", author="P", depends_on=["B"])
    assert not ok and "cycle" in message


def test_post_task_allows_dependency_on_unknown_task() -> None:
    board = Blackboard()
    ok, _ = board.post_task(task_id="A", title="a", author="P", depends_on=["ghost"])
    assert ok  # a dependency not yet on the board cannot close a cycle


def test_post_task_diamond_dependencies_revisit_shared_node() -> None:
    board = Blackboard()
    board.post_task(task_id="D", title="d", author="P")
    board.post_task(task_id="B", title="b", author="P", depends_on=["D"])
    board.post_task(task_id="C", title="c", author="P", depends_on=["D"])
    # A -> {B, C} -> D: the cycle walk reaches D twice and takes the
    # already-seen path on the second hit, finding no cycle.
    ok, _ = board.post_task(task_id="A", title="a", author="P", depends_on=["B", "C"])
    assert ok
    assert board.blocking_dependencies("A") == ["B", "C"]


# --- update_task -------------------------------------------------------------


def test_update_task_changes_status_and_owner() -> None:
    board = Blackboard()
    board.post_task(task_id="T1", title="t", author="A", now=1.0)
    ok, _ = board.update_task("T1", status="in_progress", suggested_owner="FAST", now=2.0)
    assert ok
    assert board.tasks["T1"].status == "in_progress"
    assert board.tasks["T1"].suggested_owner == "FAST"
    assert board.tasks["T1"].updated_at == 2.0


def test_update_task_owner_only_leaves_status_untouched() -> None:
    board = Blackboard()
    board.post_task(task_id="T1", title="t", author="A")
    ok, _ = board.update_task("T1", suggested_owner="FAST")  # status left as None
    assert ok
    assert board.tasks["T1"].status == "open"
    assert board.tasks["T1"].suggested_owner == "FAST"


def test_update_task_unknown_and_bad_status() -> None:
    board = Blackboard()
    ok, message = board.update_task("missing", status="done")
    assert not ok and "not on the board" in message
    board.post_task(task_id="T1", title="t", author="A")
    ok, message = board.update_task("T1", status="frozen")
    assert not ok and "Unknown ledger status" in message


# --- post_progress -----------------------------------------------------------


def test_post_progress_appends_note() -> None:
    board = Blackboard()
    ok, note = board.post_progress(task_id="T1", author="FAST", text="started", now=4.0)
    assert ok and isinstance(note, ProgressNote)
    assert note.posted_at == 4.0
    assert board.progress[-1].text == "started"


def test_post_progress_rejects_unknown_kind() -> None:
    board = Blackboard()
    ok, message = board.post_progress(task_id="T1", author="A", text="x", kind="rant")
    assert not ok and isinstance(message, str)
    assert "Unknown progress kind" in message


def test_post_progress_board_wide_note_has_empty_task() -> None:
    board = Blackboard()
    board.post_progress(task_id="  ", author="A", text="all hands", kind="assessment")
    assert board.progress[-1].task_id == ""


def test_post_progress_is_bounded() -> None:
    board = Blackboard(max_progress=2)
    for i in range(5):
        board.post_progress(task_id="T", author="A", text=str(i))
    assert [n.text for n in board.progress] == ["3", "4"]


def test_note_appends_a_plain_note_and_returns_it() -> None:
    board = Blackboard()
    note = board.note(task_id="  T1 ", author="A", text="moved", now=7.0)
    assert isinstance(note, ProgressNote)
    assert note.kind == "note"
    assert note.task_id == "T1"
    assert note.posted_at == 7.0
    assert board.progress[-1] is note


def test_note_is_bounded() -> None:
    board = Blackboard(max_progress=2)
    for i in range(5):
        board.note(task_id="T", author="A", text=str(i))
    assert [n.text for n in board.progress] == ["3", "4"]


def test_max_progress_is_clamped_to_one() -> None:
    board = Blackboard(max_progress=0)
    assert board.max_progress == 1


# --- dependencies + readiness ------------------------------------------------


def test_blocking_dependencies_unknown_task_is_empty() -> None:
    assert Blackboard().blocking_dependencies("nope") == []


def test_blocking_dependencies_reports_missing_and_unfinished() -> None:
    board = Blackboard()
    board.post_task(task_id="dep_done", title="d", author="P")
    board.update_task("dep_done", status="done")
    board.post_task(task_id="dep_open", title="o", author="P")
    board.post_task(
        task_id="T1", title="t", author="P", depends_on=["dep_done", "dep_open", "ghost"]
    )
    # dep_done is terminal (satisfied); dep_open and the missing ghost still block.
    assert board.blocking_dependencies("T1") == ["dep_open", "ghost"]


def test_ready_tasks_gates_on_dependencies_and_status() -> None:
    board = Blackboard()
    board.post_task(task_id="A", title="a", author="P")
    board.post_task(task_id="B", title="b", author="P", depends_on=["A"])
    board.post_task(task_id="C", title="c", author="P")
    board.update_task("C", status="in_progress")
    # Only A is ready: B is blocked by open A, C is not open.
    assert [t.task_id for t in board.ready_tasks()] == ["A"]

    board.update_task("A", status="done")
    # A done -> B becomes ready; A itself is no longer open.
    assert [t.task_id for t in board.ready_tasks()] == ["B"]


def test_cancelled_dependency_satisfies_readiness() -> None:
    board = Blackboard()
    board.post_task(task_id="A", title="a", author="P")
    board.update_task("A", status="cancelled")
    board.post_task(task_id="B", title="b", author="P", depends_on=["A"])
    assert [t.task_id for t in board.ready_tasks()] == ["B"]


# --- snapshot ----------------------------------------------------------------


def test_snapshot_orders_tasks_and_lists_ready_and_progress() -> None:
    board = Blackboard()
    board.post_task(task_id="B", title="b", author="P")
    board.post_task(task_id="A", title="a", author="P")
    board.post_progress(task_id="A", author="P", text="note-1")
    snap = board.snapshot()
    assert [t["task_id"] for t in snap["tasks"]] == ["A", "B"]
    assert set(snap["ready"]) == {"A", "B"}
    assert snap["progress"][-1]["text"] == "note-1"
