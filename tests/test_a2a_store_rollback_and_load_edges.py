# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — A2A task store: failed saves roll back; malformed state loads partially
"""A save the filesystem refuses leaves memory as it was; bad persisted sections are skipped."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from synapse_channel.a2a_push_delivery import PushDeliveryAttempt, PushDeliveryState
from synapse_channel.a2a_store import A2ATaskStore


def _attempt(number: int) -> PushDeliveryAttempt:
    return PushDeliveryAttempt(
        task_id="task-a",
        config_id="cfg-a",
        delivery_id="delivery-a",
        attempt=number,
        state=PushDeliveryState.RETRY_SCHEDULED,
        occurred_at=float(number),
        retry_window_seconds=5.0,
    )


@pytest.fixture
def frozen_store(tmp_path: Path) -> Iterator[tuple[A2ATaskStore, Path]]:
    """A store with one task, one attempt and one event, whose directory is then read-only."""
    directory = tmp_path / "state"
    store = A2ATaskStore(directory / "a2a.json")
    store.put({"id": "task-a", "status": {"state": "TASK_STATE_WORKING"}})
    store.record_push_delivery_attempt(_attempt(1))
    store.append_event("task-a", {"kind": "status", "n": 1})
    directory.chmod(0o500)
    try:
        yield store, directory / "a2a.json"
    finally:
        directory.chmod(0o700)


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0,
    reason="needs POSIX directory permissions that bind the current user",
)
def test_refused_saves_restore_events_and_delivery_attempts(
    frozen_store: tuple[A2ATaskStore, Path],
) -> None:
    """Neither a new event nor a new attempt survives in memory when persisting fails."""
    store, path = frozen_store
    on_disk = path.read_bytes()
    with pytest.raises(PermissionError):
        store.append_event("task-a", {"kind": "status", "n": 2})
    assert store.event_history("task-a") == [{"kind": "status", "n": 1}]
    with pytest.raises(PermissionError):
        store.record_push_delivery_attempt(_attempt(2))
    assert store.list_push_delivery_attempts("task-a") == [_attempt(1).to_json()]
    assert path.read_bytes() == on_disk


def _load(path: Path, state: dict[str, object]) -> A2ATaskStore:
    path.write_text(json.dumps(state), encoding="utf-8")
    return A2ATaskStore(path)


def test_malformed_persisted_sections_are_skipped_on_load(tmp_path: Path) -> None:
    """Non-list histories and empty cleaned histories are dropped; wrong section types ignored."""
    task = {"id": "task-a", "status": {"state": "TASK_STATE_WORKING"}}
    store = _load(
        tmp_path / "partial.json",
        {
            "tasks": {"task-a": task},
            "pushDeliveryAttempts": [_attempt(1).to_json()],
            "eventHistory": {
                "task-a": "not-a-list",
                "task-d": 5,
                "task-b": [1, "two"],
                "task-c": [{"kind": "status"}, 3],
            },
        },
    )
    assert store.all_event_history() == {"task-c": [{"kind": "status"}]}
    assert store.list_push_delivery_attempts("task-a") == []
    assert store.get("task-a") is not None
    listed = _load(tmp_path / "listed.json", {"tasks": {}, "eventHistory": [{"kind": "x"}]})
    assert listed.all_event_history() == {}
