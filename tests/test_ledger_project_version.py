# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for ledger task project scope and version CAS

from __future__ import annotations

from synapse_channel.core.ledger import Blackboard


def test_declare_defaults_to_unscoped_version_one() -> None:
    board = Blackboard()
    ok, _ = board.post_task(task_id="T1", title="work", author="A", now=1.0)
    assert ok
    task = board.tasks["T1"]
    assert task.project == ""
    assert task.version == 1
    assert task.as_dict()["project"] == ""
    assert task.as_dict()["version"] == 1


def test_declare_with_project_scope() -> None:
    board = Blackboard()
    ok, _ = board.post_task(task_id="T1", title="work", author="A", project="PROJ", now=1.0)
    assert ok
    assert board.tasks["T1"].project == "PROJ"


def test_redeclare_bumps_version_and_keeps_scope_on_blank_project() -> None:
    board = Blackboard()
    board.post_task(task_id="T1", title="work", author="A", project="PROJ", now=1.0)
    ok, _ = board.post_task(task_id="T1", title="work v2", author="A", now=2.0)
    assert ok
    task = board.tasks["T1"]
    assert task.title == "work v2"
    assert task.project == "PROJ"
    assert task.version == 2


def test_redeclare_with_same_project_is_accepted() -> None:
    board = Blackboard()
    board.post_task(task_id="T1", title="work", author="A", project="PROJ", now=1.0)
    ok, _ = board.post_task(task_id="T1", title="work", author="A", project="PROJ", now=2.0)
    assert ok
    assert board.tasks["T1"].version == 2


def test_redeclare_with_conflicting_project_fails_closed() -> None:
    board = Blackboard()
    board.post_task(task_id="T1", title="work", author="A", project="PROJ", now=1.0)
    ok, message = board.post_task(task_id="T1", title="work", author="A", project="OTHER", now=2.0)
    assert not ok
    assert "project conflict" in message
    task = board.tasks["T1"]
    assert task.project == "PROJ"
    assert task.version == 1


def test_redeclare_can_scope_an_unscoped_task() -> None:
    board = Blackboard()
    board.post_task(task_id="T1", title="work", author="A", now=1.0)
    ok, _ = board.post_task(task_id="T1", title="work", author="A", project="PROJ", now=2.0)
    assert ok
    assert board.tasks["T1"].project == "PROJ"
    assert board.tasks["T1"].version == 2


def test_update_bumps_version_and_replaces_project() -> None:
    board = Blackboard()
    board.post_task(task_id="T1", title="work", author="A", project="PROJ", now=1.0)
    ok, _ = board.update_task("T1", project="OTHER", now=2.0)
    assert ok
    task = board.tasks["T1"]
    assert task.project == "OTHER"
    assert task.version == 2


def test_update_with_blank_project_clears_scope() -> None:
    board = Blackboard()
    board.post_task(task_id="T1", title="work", author="A", project="PROJ", now=1.0)
    ok, _ = board.update_task("T1", project="", now=2.0)
    assert ok
    assert board.tasks["T1"].project == ""


def test_update_cas_match_succeeds() -> None:
    board = Blackboard()
    board.post_task(task_id="T1", title="work", author="A", now=1.0)
    ok, _ = board.update_task("T1", status="in_progress", expected_version=1, now=2.0)
    assert ok
    assert board.tasks["T1"].status == "in_progress"
    assert board.tasks["T1"].version == 2


def test_update_cas_mismatch_refuses_mutation() -> None:
    board = Blackboard()
    board.post_task(task_id="T1", title="work", author="A", now=1.0)
    ok, message = board.update_task("T1", status="done", expected_version=7, now=2.0)
    assert not ok
    assert "version conflict" in message
    assert "expected v7" in message
    assert "board has v1" in message
    task = board.tasks["T1"]
    assert task.status == "open"
    assert task.version == 1


def test_update_cas_on_missing_task_reports_board_absence() -> None:
    board = Blackboard()
    ok, message = board.update_task("T404", status="done", expected_version=0, now=1.0)
    assert not ok
    assert "not on the board" in message


def test_declare_cas_zero_creates_only_when_absent() -> None:
    board = Blackboard()
    ok, _ = board.post_task(task_id="T1", title="work", author="A", expected_version=0, now=1.0)
    assert ok
    ok, message = board.post_task(
        task_id="T1", title="work", author="A", expected_version=0, now=2.0
    )
    assert not ok
    assert "version conflict" in message
    assert board.tasks["T1"].version == 1


def test_declare_cas_match_on_existing_redeclare() -> None:
    board = Blackboard()
    board.post_task(task_id="T1", title="work", author="A", now=1.0)
    ok, _ = board.post_task(task_id="T1", title="work", author="A", expected_version=1, now=2.0)
    assert ok
    assert board.tasks["T1"].version == 2


def test_declare_cas_mismatch_on_existing_refuses() -> None:
    board = Blackboard()
    board.post_task(task_id="T1", title="work", author="A", now=1.0)
    ok, message = board.post_task(
        task_id="T1", title="work", author="A", expected_version=5, now=2.0
    )
    assert not ok
    assert "version conflict" in message
    assert board.tasks["T1"].version == 1


def test_declare_cas_runs_before_cycle_or_project_conflict() -> None:
    board = Blackboard()
    board.post_task(task_id="T1", title="work", author="A", project="PROJ", now=1.0)
    ok, message = board.post_task(
        task_id="T1", title="work", author="A", project="OTHER", expected_version=99, now=2.0
    )
    assert not ok
    assert "version conflict" in message


def test_ready_tasks_carries_project_scoped_tasks_unchanged() -> None:
    board = Blackboard()
    board.post_task(task_id="T1", title="work", author="A", project="PROJ", now=1.0)
    board.post_task(task_id="T2", title="other", author="A", now=1.5)
    ready = board.ready_tasks()
    assert [task.task_id for task in ready] == ["T1", "T2"]
