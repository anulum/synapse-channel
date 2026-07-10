# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for A2A bridge task storage

from __future__ import annotations

import json
import os
import stat
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from synapse_channel.a2a_errors import A2AQuotaError, A2AStoreError
from synapse_channel.a2a_store import A2ATaskStore, _fsync_parent


def _stored_task(
    task_id: str,
    *,
    state: str = "TASK_STATE_COMPLETED",
    updated_at: float = 0.0,
    history: list[object] | None = None,
    artifacts: list[object] | None = None,
) -> dict[str, object]:
    return {
        "id": task_id,
        "status": {"state": state},
        "history": history if history is not None else [],
        "artifacts": artifacts if artifacts is not None else [],
        "metadata": {"updatedAt": updated_at},
    }


@contextmanager
def _permissive_umask() -> Iterator[None]:
    previous = os.umask(0)
    try:
        yield
    finally:
        os.umask(previous)


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def _writer_failing_after(successes: int) -> Callable[[Path, str], None]:
    calls = 0

    def write_state(path: Path, payload: str) -> None:
        nonlocal calls
        calls += 1
        if calls > successes:
            raise OSError(f"blocked write to {path}")
        path.write_text(payload, encoding="utf-8")

    return write_state


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file permissions")
def test_a2a_task_store_state_file_is_owner_only(tmp_path: Path) -> None:
    storage_path = tmp_path / "a2a-state.json"
    store = A2ATaskStore(storage_path)

    with _permissive_umask():
        store.put({"id": "task-a", "status": {"state": "TASK_STATE_COMPLETED"}})

    assert _mode(storage_path) & 0o077 == 0


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX fsync semantics")
def test_a2a_task_store_fsyncs_state_file_and_parent_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage_path = tmp_path / "a2a-state.json"
    fsync_calls: list[int] = []
    monkeypatch.setattr(os, "fsync", fsync_calls.append)
    store = A2ATaskStore(storage_path)

    store.put({"id": "task-a", "status": {"state": "TASK_STATE_COMPLETED"}})

    assert len(fsync_calls) >= 2


def test_a2a_task_store_parent_fsync_noops_on_non_posix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_open(*_args: object, **_kwargs: object) -> int:
        raise AssertionError("directory fsync should not open paths on non-POSIX platforms")

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(os, "open", fail_open)

    _fsync_parent(tmp_path / "a2a-state.json")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file permissions")
def test_a2a_task_store_temp_file_is_owner_only_after_failed_write(tmp_path: Path) -> None:
    storage_path = tmp_path / "a2a-state.json"
    temp_state_path = storage_path.with_suffix(".json.tmp")

    def write_then_fail(path: Path, payload: str) -> None:
        path.write_text(payload, encoding="utf-8")
        raise OSError(f"blocked write to {path}")

    store = A2ATaskStore(storage_path, state_writer=write_then_fail)

    with pytest.raises(OSError, match="blocked write"):
        with _permissive_umask():
            store.put({"id": "task-a", "status": {"state": "TASK_STATE_COMPLETED"}})

    assert _mode(temp_state_path) & 0o077 == 0
    assert not storage_path.exists()


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


def test_a2a_task_store_bounds_stored_tasks_and_removes_push_configs() -> None:
    store = A2ATaskStore(max_tasks=2)
    store.put(_stored_task("old", updated_at=1.0))
    store.put_push_config("old", {"id": "cfg-old", "webhookUrl": "https://example.test/old"})
    store.put(_stored_task("middle", updated_at=2.0))
    store.put(_stored_task("new", updated_at=3.0))

    assert [task["id"] for task in store.list_tasks()] == ["middle", "new"]
    assert store.get("old") is None
    assert store.list_push_configs("old") == []


def test_a2a_task_store_rolls_back_quota_eviction_when_save_fails(tmp_path: Path) -> None:
    storage_path = tmp_path / "a2a-state.json"
    store = A2ATaskStore(storage_path, max_tasks=1, state_writer=_writer_failing_after(1))
    original = store.put(_stored_task("original", updated_at=1.0))

    with pytest.raises(OSError, match="blocked write"):
        store.put(_stored_task("replacement", updated_at=2.0))

    assert store.get("original") == original
    assert store.get("replacement") is None


def test_a2a_task_store_prunes_expired_terminal_tasks() -> None:
    store = A2ATaskStore(retention_seconds=10.0)
    store.put(_stored_task("old-terminal", updated_at=5.0))
    store.put_push_config(
        "old-terminal",
        {"id": "cfg-old", "webhookUrl": "https://example.test/old"},
    )
    store.put(_stored_task("old-open", state="TASK_STATE_WORKING", updated_at=5.0))
    store.put(_stored_task("recent-terminal", updated_at=12.0))

    removed = store.prune_expired(now=16.0)

    assert removed == ["old-terminal"]
    assert store.get("old-terminal") is None
    assert store.list_push_configs("old-terminal") == []
    assert store.get("old-open") is not None
    assert store.get("recent-terminal") is not None


def test_a2a_task_store_prunes_tasks_with_malformed_retention_timestamps() -> None:
    store = A2ATaskStore(retention_seconds=1.0)
    store.put(
        {
            "id": "bad-metadata",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "metadata": "bad",
        }
    )
    store.put(
        {
            "id": "bad-updated-at",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "metadata": {"updatedAt": "not-a-float"},
        }
    )

    assert store.prune_expired(now=2.0) == ["bad-metadata", "bad-updated-at"]
    assert store.list_tasks() == []


def test_a2a_task_store_rolls_back_expiry_prune_when_save_fails(tmp_path: Path) -> None:
    storage_path = tmp_path / "a2a-state.json"
    store = A2ATaskStore(storage_path, retention_seconds=1.0, state_writer=_writer_failing_after(1))
    original = store.put(_stored_task("old-terminal", updated_at=1.0))

    with pytest.raises(OSError, match="blocked write"):
        store.prune_expired(now=3.0)

    assert store.get("old-terminal") == original


def test_a2a_task_store_bounds_history_artifacts_and_push_configs() -> None:
    store = A2ATaskStore(
        max_task_history=2,
        max_task_artifacts=1,
        max_push_configs_per_task=1,
    )
    store.put(
        _stored_task(
            "task-a",
            history=[{"messageId": "m1"}, {"messageId": "m2"}, {"messageId": "m3"}],
            artifacts=[{"artifactId": "a1"}, {"artifactId": "a2"}],
        )
    )
    store.put_push_config("task-a", {"id": "cfg-a", "webhookUrl": "https://example.test/a"})

    stored = store.get("task-a")
    assert stored is not None
    assert stored["history"] == [{"messageId": "m2"}, {"messageId": "m3"}]
    assert stored["artifacts"] == [{"artifactId": "a2"}]
    with pytest.raises(A2AQuotaError, match="pushNotificationConfig limit exceeded"):
        store.put_push_config("task-a", {"id": "cfg-b", "webhookUrl": "https://example.test/b"})


def test_a2a_task_store_rejects_invalid_state_file(tmp_path: Path) -> None:
    storage_path = tmp_path / "a2a-state.json"
    storage_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(A2AStoreError, match="Invalid A2A state file"):
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


def test_a2a_task_store_keeps_committed_state_file_when_temp_write_fails(
    tmp_path: Path,
) -> None:
    storage_path = tmp_path / "a2a-state.json"
    calls = 0

    def write_state(path: Path, payload: str) -> None:
        nonlocal calls
        calls += 1
        path.write_text(payload, encoding="utf-8")
        if calls > 1:
            raise OSError(f"blocked write to {path}")

    store = A2ATaskStore(storage_path, state_writer=write_state)
    original = store.put({"id": "task-a", "status": {"state": "TASK_STATE_WORKING"}})
    committed_payload = storage_path.read_text(encoding="utf-8")

    with pytest.raises(OSError, match="blocked write"):
        store.put({"id": "task-b", "status": {"state": "TASK_STATE_COMPLETED"}})

    assert store.get("task-a") == original
    assert store.get("task-b") is None
    assert storage_path.read_text(encoding="utf-8") == committed_payload


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


def test_a2a_task_store_prunes_overflow_and_nan_retention_timestamps() -> None:
    """A JSON-integer-too-large or NaN stamp reads as expired, never a crash or NaN sort key."""
    store = A2ATaskStore(retention_seconds=1.0)
    store.put(
        {
            "id": "huge-stamp",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "metadata": {"updatedAt": 10**400},
        }
    )
    store.put(
        {
            "id": "nan-stamp",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "metadata": {"updatedAt": float("nan")},
        }
    )

    assert store.prune_expired(now=2.0) == ["huge-stamp", "nan-stamp"]
    assert store.list_tasks() == []
