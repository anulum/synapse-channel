# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Agent2Agent bridge task events
"""In-process task-event fanout for the Agent2Agent bridge.

The bridge intentionally keeps subscription replay in local process memory. The
task store is responsible for durable task snapshots, while this module only
serves bounded replay for subscribers attached to the currently running bridge.
"""

from __future__ import annotations

import copy
import queue
import threading
from collections.abc import Iterable

from synapse_channel.a2a import JsonMap
from synapse_channel.a2a_validation import TERMINAL_TASK_STATES


class A2ATaskEvents:
    """Bounded, memory-only subscribers for A2A task lifecycle updates."""

    def __init__(self, *, max_history_events: int = 64) -> None:
        self.max_history_events = max(max_history_events, 1)
        self._subscribers: dict[str, list[queue.Queue[JsonMap]]] = {}
        self._history: dict[str, list[JsonMap]] = {}
        self._lock = threading.RLock()

    def publish(self, task_id: str, task: JsonMap) -> None:
        """Publish one task update to local subscribers and replay history."""
        event = self._event(task)
        with self._lock:
            history = self._history.setdefault(task_id, [])
            history.append(copy.deepcopy(event))
            del history[: -self.max_history_events]
            subscribers = list(self._subscribers.get(task_id, []))
        for subscriber in subscribers:
            subscriber.put(copy.deepcopy(event))

    def drop(self, task_ids: Iterable[str]) -> None:
        """Drop memory-only replay history and subscribers for removed tasks."""
        with self._lock:
            for task_id in task_ids:
                self._history.pop(task_id, None)
                self._subscribers.pop(task_id, None)

    def subscribe(
        self,
        task_id: str,
        task: JsonMap,
        *,
        wait_seconds: float | None,
        default_wait_seconds: float,
    ) -> list[JsonMap]:
        """Return bounded in-process replay plus queued updates for one subscription."""
        updates: queue.Queue[JsonMap] = queue.Queue()
        current_event = self._event(task)
        current_state = self._last_state([current_event])
        if current_state in TERMINAL_TASK_STATES:
            return [current_event]
        with self._lock:
            events = copy.deepcopy(self._history.get(task_id, []))
            if not events:
                events = [current_event]
            elif self._last_state(events) in TERMINAL_TASK_STATES:
                events.insert(0, current_event)
            state = self._last_state(events)
            if state not in TERMINAL_TASK_STATES:
                self._subscribers.setdefault(task_id, []).append(updates)
        if state in TERMINAL_TASK_STATES:
            return events
        timeout = default_wait_seconds if wait_seconds is None else max(wait_seconds, 0.0)
        try:
            if timeout > 0.0:
                try:
                    events.append(updates.get(timeout=timeout))
                except queue.Empty:
                    pass
        finally:
            with self._lock:
                subscribers = self._subscribers.get(task_id, [])
                if updates in subscribers:
                    subscribers.remove(updates)
                if not subscribers and task_id in self._subscribers:
                    del self._subscribers[task_id]
        return events

    def _event(self, task: JsonMap) -> JsonMap:
        return {"task": copy.deepcopy(task)}

    def _last_state(self, events: list[JsonMap]) -> str:
        if not events:
            return ""
        task = events[-1].get("task")
        if not isinstance(task, dict):
            return ""
        status = task.get("status")
        if not isinstance(status, dict):
            return ""
        return str(status.get("state", ""))
