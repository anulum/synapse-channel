# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dedicated tests for the shared-plan (ledger) handlers

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any, cast

import pytest

from synapse_channel.core.handlers import planning
from synapse_channel.core.ledger import ProgressNote
from synapse_channel.core.protocol import MessageType

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub


class _FakeTask:
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.task_id}


class _FakeBlackboard:
    """Configurable blackboard stand-in that records the write it received."""

    def __init__(
        self,
        *,
        post_task_result: tuple[bool, str] = (True, "posted"),
        update_result: tuple[bool, str] = (True, "updated"),
        progress_result: tuple[bool, Any] = (True, None),
    ) -> None:
        self._post_task_result = post_task_result
        self._update_result = update_result
        self._progress_result = progress_result
        self.tasks: dict[str, _FakeTask] = {}
        self.calls: dict[str, dict[str, Any]] = {}

    def post_task(self, **kwargs: Any) -> tuple[bool, str]:
        self.calls["post_task"] = kwargs
        ok, message = self._post_task_result
        if ok:
            self.tasks[kwargs["task_id"]] = _FakeTask(kwargs["task_id"])
        return ok, message

    def update_task(self, task_id: str, **kwargs: Any) -> tuple[bool, str]:
        self.calls["update_task"] = {"task_id": task_id, **kwargs}
        ok, message = self._update_result
        if ok:
            self.tasks[task_id] = _FakeTask(task_id)
        return ok, message

    def post_progress(self, **kwargs: Any) -> tuple[bool, Any]:
        self.calls["post_progress"] = kwargs
        return self._progress_result


class _FakeHub:
    def __init__(self, blackboard: _FakeBlackboard, *, journal: Any = None) -> None:
        self.blackboard = blackboard
        self.journal = journal
        self.broadcasts: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []

    def _system(self, text: str, **fields: Any) -> dict[str, Any]:
        return {"text": text, **fields}

    async def _broadcast(self, payload: dict[str, Any]) -> None:
        self.broadcasts.append(payload)

    async def _send_json(self, websocket: Any, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


def _as_hub(hub: _FakeHub) -> SynapseHub:
    """Present the structural fake as a concrete hub without a type: ignore."""
    return cast("SynapseHub", hub)


def _capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    log: dict[str, list[Any]] = {"task": [], "progress": []}

    def _task(journal: Any, payload: Any) -> None:
        log["task"].append(payload)

    def _progress(journal: Any, payload: Any) -> None:
        log["progress"].append(payload)

    monkeypatch.setattr(planning, "record_ledger_task", _task)
    monkeypatch.setattr(planning, "record_ledger_progress", _progress)
    return log


class TestHandleLedgerTask:
    async def test_accepted_task_is_journalled_and_broadcast(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = _capture(monkeypatch)
        board = _FakeBlackboard()
        hub = _FakeHub(board, journal=object())
        await planning.handle_ledger_task(
            _as_hub(hub),
            "alice",
            {"task_id": " t1 ", "title": "Do", "depends_on": ["a", "b"]},
            object(),
        )
        assert board.calls["post_task"]["task_id"] == "t1"
        assert board.calls["post_task"]["depends_on"] == ["a", "b"]
        assert board.calls["post_task"]["author"] == "alice"
        assert hub.broadcasts[0]["msg_type"] == MessageType.LEDGER_TASK_POSTED
        assert hub.broadcasts[0]["task"] == {"id": "t1"}
        assert len(log["task"]) == 1

    async def test_non_list_depends_on_becomes_empty_and_skips_journal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = _capture(monkeypatch)
        board = _FakeBlackboard()
        hub = _FakeHub(board, journal=None)
        await planning.handle_ledger_task(
            _as_hub(hub), "alice", {"task_id": "t1", "depends_on": "nope"}, object()
        )
        assert board.calls["post_task"]["depends_on"] == []
        assert hub.broadcasts
        assert log["task"] == []

    async def test_rejected_task_is_reported_privately(self) -> None:
        board = _FakeBlackboard(post_task_result=(False, "duplicate id"))
        hub = _FakeHub(board)
        await planning.handle_ledger_task(_as_hub(hub), "alice", {"task_id": "t1"}, object())
        assert hub.broadcasts == []
        assert hub.sent[0]["msg_type"] == MessageType.ERROR
        assert hub.sent[0]["target"] == "alice"
        assert hub.sent[0]["text"] == "duplicate id"


class TestHandleLedgerTaskUpdate:
    async def test_accepted_update_is_journalled_and_broadcast(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = _capture(monkeypatch)
        board = _FakeBlackboard()
        hub = _FakeHub(board, journal=object())
        await planning.handle_ledger_task_update(
            _as_hub(hub),
            "alice",
            {"task_id": "t1", "status": "done", "suggested_owner": "bob"},
            object(),
        )
        assert board.calls["update_task"] == {
            "task_id": "t1",
            "status": "done",
            "suggested_owner": "bob",
            "project": None,
            "expected_version": None,
        }
        assert hub.broadcasts[0]["msg_type"] == MessageType.LEDGER_TASK_UPDATED
        assert len(log["task"]) == 1

    async def test_absent_status_and_owner_pass_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        log = _capture(monkeypatch)
        board = _FakeBlackboard()
        hub = _FakeHub(board, journal=None)
        await planning.handle_ledger_task_update(_as_hub(hub), "alice", {"task_id": "t1"}, object())
        assert board.calls["update_task"] == {
            "task_id": "t1",
            "status": None,
            "suggested_owner": None,
            "project": None,
            "expected_version": None,
        }
        assert hub.broadcasts
        assert log["task"] == []

    async def test_rejected_update_is_reported_privately(self) -> None:
        board = _FakeBlackboard(update_result=(False, "unknown task"))
        hub = _FakeHub(board)
        await planning.handle_ledger_task_update(_as_hub(hub), "alice", {"task_id": "t1"}, object())
        assert hub.broadcasts == []
        assert hub.sent[0]["msg_type"] == MessageType.ERROR
        assert hub.sent[0]["text"] == "unknown task"


class TestHandleLedgerProgress:
    def _note(self) -> ProgressNote:
        return ProgressNote(
            task_id="t1", author="alice", kind="note", text="progress", posted_at=1.0
        )

    async def test_accepted_progress_is_journalled_and_broadcast(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = _capture(monkeypatch)
        note = self._note()
        board = _FakeBlackboard(progress_result=(True, note))
        hub = _FakeHub(board, journal=object())
        await planning.handle_ledger_progress(
            _as_hub(hub),
            "alice",
            {"task_id": "t1", "payload": "progress", "kind": "note"},
            object(),
        )
        # ``text`` falls back to ``payload`` when ``text`` is absent.
        assert board.calls["post_progress"]["text"] == "progress"
        assert hub.broadcasts[0]["msg_type"] == MessageType.LEDGER_PROGRESS_POSTED
        assert hub.broadcasts[0]["text"] == "Progress from alice"
        assert hub.broadcasts[0]["note"] == note.as_dict()
        assert len(log["progress"]) == 1

    async def test_accepted_progress_without_journal_skips_record(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = _capture(monkeypatch)
        board = _FakeBlackboard(progress_result=(True, self._note()))
        hub = _FakeHub(board, journal=None)
        await planning.handle_ledger_progress(
            _as_hub(hub), "alice", {"task_id": "t1", "text": "hi"}, object()
        )
        assert board.calls["post_progress"]["text"] == "hi"
        assert hub.broadcasts
        assert log["progress"] == []

    async def test_rejected_kind_is_reported_privately(self) -> None:
        board = _FakeBlackboard(progress_result=(False, "unknown kind"))
        hub = _FakeHub(board)
        await planning.handle_ledger_progress(_as_hub(hub), "alice", {"task_id": "t1"}, object())
        assert hub.broadcasts == []
        assert hub.sent[0]["msg_type"] == MessageType.ERROR
        assert hub.sent[0]["text"] == "unknown kind"

    async def test_non_progress_note_result_is_rejected(self) -> None:
        # ok is True but the result is not a ProgressNote -> still a private error.
        board = _FakeBlackboard(progress_result=(True, "not-a-note"))
        hub = _FakeHub(board)
        await planning.handle_ledger_progress(_as_hub(hub), "alice", {"task_id": "t1"}, object())
        assert hub.broadcasts == []
        assert hub.sent[0]["msg_type"] == MessageType.ERROR
        assert hub.sent[0]["text"] == "not-a-note"


class TestJournalFailureRollback:
    """A journal append failure must roll the mutation back completely."""

    async def test_declare_new_task_is_popped_on_journal_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise(_journal: Any, _payload: Any) -> None:
            raise sqlite3.OperationalError("disk full")

        monkeypatch.setattr(planning, "record_ledger_task", _raise)
        board = _FakeBlackboard()
        hub = _FakeHub(board, journal=object())
        await planning.handle_ledger_task(
            _as_hub(hub), "alice", {"task_id": "t1", "title": "Do"}, object()
        )
        assert "t1" not in board.tasks
        assert hub.broadcasts == []
        assert "rolled back" in hub.sent[-1]["text"]

    async def test_declare_existing_task_is_restored_on_journal_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise(_journal: Any, _payload: Any) -> None:
            raise sqlite3.OperationalError("disk full")

        monkeypatch.setattr(planning, "record_ledger_task", _raise)
        board = _FakeBlackboard()
        prior = _FakeTask("t1")
        prior.marker = "pre-mutation"  # type: ignore[attr-defined]
        board.tasks["t1"] = prior
        hub = _FakeHub(board, journal=object())
        await planning.handle_ledger_task(
            _as_hub(hub), "alice", {"task_id": "t1", "title": "Do"}, object()
        )
        restored = board.tasks["t1"]
        assert getattr(restored, "marker", None) == "pre-mutation"
        assert hub.broadcasts == []
        assert "rolled back" in hub.sent[-1]["text"]

    async def test_update_is_restored_on_journal_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise(_journal: Any, _payload: Any) -> None:
            raise sqlite3.OperationalError("disk full")

        monkeypatch.setattr(planning, "record_ledger_task", _raise)
        board = _FakeBlackboard()
        prior = _FakeTask("t1")
        prior.marker = "pre-mutation"  # type: ignore[attr-defined]
        board.tasks["t1"] = prior
        hub = _FakeHub(board, journal=object())
        await planning.handle_ledger_task_update(
            _as_hub(hub), "alice", {"task_id": "t1", "status": "done"}, object()
        )
        restored = board.tasks["t1"]
        assert getattr(restored, "marker", None) == "pre-mutation"
        assert hub.broadcasts == []
        assert "rolled back" in hub.sent[-1]["text"]
