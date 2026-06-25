# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for A2A bridge task storage

from __future__ import annotations

import json
from pathlib import Path

from synapse_channel.a2a_store import A2ATaskStore


def test_a2a_task_store_import_boundary_is_stable() -> None:
    store = A2ATaskStore()

    store.put({"id": "task-a", "status": {"state": "TASK_STATE_WORKING"}})

    assert store.get("task-a") is not None


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
