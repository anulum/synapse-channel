# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable list ordering + multi-process stream replay
"""Production-path tests for timestamp list order and durable event replay."""

from __future__ import annotations

from pathlib import Path

from a2a_server_helpers import RecordingAgent
from synapse_channel.a2a_events import A2ATaskEvents
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore


def test_list_tasks_orders_by_updated_at_descending() -> None:
    store = A2ATaskStore()
    older = {
        "id": "task-old",
        "status": {"state": "TASK_STATE_WORKING"},
        "metadata": {"updatedAt": 100.0, "createdAt": 100.0},
        "history": [],
        "artifacts": [],
    }
    newer = {
        "id": "task-new",
        "status": {"state": "TASK_STATE_WORKING"},
        "metadata": {"updatedAt": 200.0, "createdAt": 200.0},
        "history": [],
        "artifacts": [],
    }
    mid = {
        "id": "task-mid",
        "status": {"state": "TASK_STATE_WORKING"},
        "metadata": {"updatedAt": 150.0, "createdAt": 150.0},
        "history": [],
        "artifacts": [],
    }
    store.put(older)
    store.put(newer)
    store.put(mid)
    ids = [str(task["id"]) for task in store.list_tasks()]
    assert ids == ["task-new", "task-mid", "task-old"]


def test_list_tasks_stable_tie_break_by_id() -> None:
    store = A2ATaskStore()
    for task_id in ("b-task", "a-task"):
        store.put(
            {
                "id": task_id,
                "status": {"state": "TASK_STATE_WORKING"},
                "metadata": {"updatedAt": 50.0, "createdAt": 50.0},
                "history": [],
                "artifacts": [],
            }
        )
    ids = [str(task["id"]) for task in store.list_tasks()]
    assert ids == ["a-task", "b-task"]


def test_bridge_list_tasks_uses_store_timestamp_order() -> None:
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card={"name": "list-order"},
        target="WORKER",
        store=A2ATaskStore(),
    )
    bridge.store.put(
        {
            "id": "z-early",
            "status": {"state": "TASK_STATE_WORKING"},
            "metadata": {"updatedAt": 1.0, "createdAt": 1.0},
            "history": [],
            "artifacts": [],
        }
    )
    bridge.store.put(
        {
            "id": "a-late",
            "status": {"state": "TASK_STATE_WORKING"},
            "metadata": {"updatedAt": 9.0, "createdAt": 9.0},
            "history": [],
            "artifacts": [],
        }
    )
    listed = bridge.list_tasks()
    assert [t["id"] for t in listed["tasks"]] == ["a-late", "z-early"]


def test_durable_event_history_survives_store_reload(tmp_path: Path) -> None:
    state = tmp_path / "a2a-state.json"
    store = A2ATaskStore(storage_path=state)
    events = A2ATaskEvents(durable_store=store)
    task = {
        "id": "task-d",
        "status": {"state": "TASK_STATE_WORKING"},
        "metadata": {"updatedAt": 1.0, "createdAt": 1.0},
        "history": [],
        "artifacts": [],
    }
    store.put(task)
    events.publish("task-d", task)
    events.publish(
        "task-d",
        {
            **task,
            "status": {"state": "TASK_STATE_WORKING"},
            "metadata": {"updatedAt": 2.0, "createdAt": 1.0},
        },
    )
    assert len(store.event_history("task-d")) == 2

    reloaded = A2ATaskStore(storage_path=state)
    assert len(reloaded.event_history("task-d")) == 2
    restarted = A2ATaskEvents(durable_store=reloaded)
    history = restarted.history_for("task-d")
    assert len(history) == 2
    assert history[-1]["task"]["id"] == "task-d"


def test_subscribe_replays_durable_history_after_restart(tmp_path: Path) -> None:
    """Production subscribe after state-file restart must return ordered durable events.

    Open tasks are recovered as FAILED on load; that must not discard prior
    WORKING lifecycle events stored in durable eventHistory.
    """
    state = tmp_path / "a2a-state.json"
    store = A2ATaskStore(storage_path=state)
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card={"name": "durable"},
        target="WORKER",
        store=store,
    )
    working = bridge.create_working_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "work"}],
            "taskId": "task-replay",
            "contextId": "ctx-replay",
        }
    )
    task_id = str(working["id"])
    bridge._set_task_status(
        working,
        state="TASK_STATE_WORKING",
        message={"messageId": "m2", "role": "ROLE_AGENT", "parts": [{"text": "progress"}]},
    )
    durable_before = store.event_history(task_id)
    assert len(durable_before) >= 2
    pre_states = [str(e["task"]["status"]["state"]) for e in durable_before]
    assert pre_states.count("TASK_STATE_WORKING") >= 1

    # Multi-process restart: new store + bridge load the same state file.
    store2 = A2ATaskStore(storage_path=state)
    bridge2 = A2ABridge(
        agent=RecordingAgent(),
        agent_card={"name": "durable-2"},
        target="WORKER",
        store=store2,
    )
    recovered = store2.get(task_id)
    assert recovered is not None
    # Restart recovery marks in-flight tasks failed (production behaviour).
    assert recovered["status"]["state"] == "TASK_STATE_FAILED"
    assert len(store2.event_history(task_id)) >= 2

    # Real public entry: subscribe_task_events (same path as SSE :subscribe).
    events = bridge2.subscribe_task_events(task_id, wait_seconds=0.0)
    assert events is not None
    assert len(events) >= 2
    assert all(event["task"]["id"] == task_id for event in events)
    states = [str(event["task"]["status"]["state"]) for event in events]
    # Prior WORKING lifecycle must appear before the recovered FAILED snapshot.
    assert "TASK_STATE_WORKING" in states
    assert states[-1] == "TASK_STATE_FAILED"
    first_working = states.index("TASK_STATE_WORKING")
    last_failed = len(states) - 1 - states[::-1].index("TASK_STATE_FAILED")
    assert first_working < last_failed
