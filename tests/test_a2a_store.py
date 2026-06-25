# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for A2A bridge task storage

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from synapse_channel.a2a_store import A2ATaskStore


def _writer_failing_after(successes: int) -> Callable[[Path, str], None]:
    calls = 0

    def write_state(path: Path, payload: str) -> None:
        nonlocal calls
        calls += 1
        if calls > successes:
            raise OSError(f"blocked write to {path}")
        path.write_text(payload, encoding="utf-8")

    return write_state


def test_a2a_task_store_import_boundary_is_stable() -> None:
    store = A2ATaskStore()

    store.put({"id": "task-a", "status": {"state": "TASK_STATE_WORKING"}})

    assert store.get("task-a") is not None


def test_a2a_task_store_lists_tasks_by_state_and_id() -> None:
    store = A2ATaskStore()
    store.put({"id": "task-b", "status": {"state": "TASK_STATE_COMPLETED"}})
    store.put({"id": "task-a", "status": {"state": "TASK_STATE_WORKING"}})

    assert [task["id"] for task in store.list_tasks()] == ["task-a", "task-b"]
    assert [task["id"] for task in store.list_tasks(state="TASK_STATE_WORKING")] == ["task-a"]


def test_a2a_task_store_rejects_invalid_state_file(tmp_path: Path) -> None:
    storage_path = tmp_path / "a2a-state.json"
    storage_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid A2A state file"):
        A2ATaskStore(storage_path)


def test_a2a_task_store_ignores_malformed_persisted_sections(tmp_path: Path) -> None:
    storage_path = tmp_path / "a2a-state.json"
    storage_path.write_text(
        json.dumps(
            {
                "tasks": {
                    "valid": {"id": "valid", "status": {}},
                    "invalid": "bad",
                },
                "pushConfigs": {
                    "task-a": {"cfg-a": {"id": "cfg-a"}, "cfg-b": "bad"},
                    "task-b": "bad",
                },
            }
        ),
        encoding="utf-8",
    )

    store = A2ATaskStore(storage_path)

    assert store.get("valid") == {"id": "valid", "status": {}}
    assert store.get("invalid") is None
    assert store.list_push_configs("task-a") == [{"id": "cfg-a"}]
    assert store.list_push_configs("task-b") == []


def test_a2a_task_store_ignores_non_mapping_persisted_sections(tmp_path: Path) -> None:
    storage_path = tmp_path / "a2a-state.json"
    storage_path.write_text(json.dumps({"tasks": [], "pushConfigs": []}), encoding="utf-8")

    store = A2ATaskStore(storage_path)

    assert store.list_tasks() == []
    assert store.list_push_configs("task-a") == []


def test_a2a_task_store_marks_stale_inflight_tasks_failed(tmp_path: Path) -> None:
    storage_path = tmp_path / "a2a-state.json"
    storage_path.write_text(
        json.dumps(
            {
                "tasks": {
                    "task-a": {"id": "task-a", "status": {"state": "TASK_STATE_WORKING"}},
                    "task-b": {"id": "task-b", "status": {"state": "TASK_STATE_COMPLETED"}},
                },
                "pushConfigs": {},
            }
        ),
        encoding="utf-8",
    )

    store = A2ATaskStore(storage_path)

    assert store.get("task-a") == {
        "id": "task-a",
        "status": {
            "state": "TASK_STATE_FAILED",
            "message": "Recovered from stale in-flight task state after restart",
        },
    }
    assert store.get("task-b") == {"id": "task-b", "status": {"state": "TASK_STATE_COMPLETED"}}


def test_a2a_task_store_keeps_tasks_without_status_unchanged(tmp_path: Path) -> None:
    storage_path = tmp_path / "a2a-state.json"
    storage_path.write_text(
        json.dumps({"tasks": {"task-a": {"id": "task-a", "status": "bad"}}, "pushConfigs": {}}),
        encoding="utf-8",
    )

    store = A2ATaskStore(storage_path)

    assert store.get("task-a") == {"id": "task-a", "status": "bad"}


def test_a2a_task_store_rolls_back_task_when_save_fails(tmp_path: Path) -> None:
    storage_path = tmp_path / "a2a-state.json"
    store = A2ATaskStore(storage_path, state_writer=_writer_failing_after(0))

    with pytest.raises(OSError, match="blocked write"):
        store.put({"id": "task-a", "status": {"state": "TASK_STATE_COMPLETED"}})

    assert store.get("task-a") is None


def test_a2a_task_store_rolls_back_existing_task_when_save_fails(tmp_path: Path) -> None:
    storage_path = tmp_path / "a2a-state.json"
    store = A2ATaskStore(storage_path, state_writer=_writer_failing_after(1))
    original = store.put({"id": "task-a", "status": {"state": "TASK_STATE_WORKING"}})

    with pytest.raises(OSError, match="blocked write"):
        store.put({"id": "task-a", "status": {"state": "TASK_STATE_COMPLETED"}})

    assert store.get("task-a") == original


def test_a2a_task_store_rolls_back_push_config_when_save_fails(tmp_path: Path) -> None:
    storage_path = tmp_path / "a2a-state.json"
    store = A2ATaskStore(storage_path, state_writer=_writer_failing_after(0))

    with pytest.raises(OSError, match="blocked write"):
        store.put_push_config("task-a", {"url": "https://example.test/hook"})

    assert store.list_push_configs("task-a") == []


def test_a2a_task_store_rolls_back_existing_push_config_when_save_fails(tmp_path: Path) -> None:
    storage_path = tmp_path / "a2a-state.json"
    store = A2ATaskStore(storage_path, state_writer=_writer_failing_after(1))
    original = store.put_push_config("task-a", {"id": "cfg-a", "url": "https://example.test/a"})

    with pytest.raises(OSError, match="blocked write"):
        store.put_push_config("task-a", {"id": "cfg-b", "url": "https://example.test/b"})

    assert store.list_push_configs("task-a") == [original]


def test_a2a_task_store_push_config_get_list_delete_paths(tmp_path: Path) -> None:
    store = A2ATaskStore(tmp_path / "a2a-state.json")

    stored = store.put_push_config("task-a", {"id": "cfg-a", "url": "https://example.test/hook"})

    assert store.get_push_config("task-a", "cfg-a") == stored
    assert store.get_push_config("task-a", "missing") is None
    assert store.list_push_configs("task-a") == [stored]
    assert store.delete_push_config("task-a", "missing") is False
    assert store.delete_push_config("missing", "cfg-a") is False
    assert store.delete_push_config("task-a", "cfg-a") is True
    assert store.list_push_configs("task-a") == []


def test_a2a_task_store_rolls_back_push_config_delete_when_save_fails(tmp_path: Path) -> None:
    storage_path = tmp_path / "a2a-state.json"
    store = A2ATaskStore(storage_path, state_writer=_writer_failing_after(1))
    stored = store.put_push_config("task-a", {"id": "cfg-a", "url": "https://example.test/hook"})

    with pytest.raises(OSError, match="blocked write"):
        store.delete_push_config("task-a", "cfg-a")

    assert store.list_push_configs("task-a") == [stored]
