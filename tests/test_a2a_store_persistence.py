# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the A2A HTTP+JSON bridge

from __future__ import annotations

from pathlib import Path

from synapse_channel.a2a_store import A2ATaskStore


def test_task_store_persists_tasks_and_push_configs(tmp_path: Path) -> None:
    state_file = tmp_path / "a2a-state.json"
    first_store = A2ATaskStore(storage_path=state_file)
    first_store.put(
        {
            "id": "task-a",
            "contextId": "ctx",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "history": [],
        }
    )
    first_store.put_push_config(
        "task-a",
        {"id": "cfg-a", "webhookUrl": "https://example.test/hook"},
    )

    second_store = A2ATaskStore(storage_path=state_file)

    assert second_store.get("task-a") is not None
    assert second_store.get_push_config("task-a", "cfg-a") is not None


def test_task_store_reports_corrupt_state_file(tmp_path: Path) -> None:
    state_file = tmp_path / "a2a-state.json"
    state_file.write_text("{not valid json", encoding="utf-8")

    try:
        A2ATaskStore(storage_path=state_file)
    except ValueError as exc:
        assert "Invalid A2A state file" in str(exc)
        assert str(state_file) in str(exc)
    else:
        raise AssertionError("corrupt A2A state file was accepted")
