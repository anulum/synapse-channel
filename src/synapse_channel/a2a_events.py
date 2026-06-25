# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Agent2Agent bridge task events
"""In-process task-event fanout for the Agent2Agent bridge."""

from __future__ import annotations

import copy
import queue

from synapse_channel.a2a import JsonMap
from synapse_channel.a2a_validation import TERMINAL_TASK_STATES


class A2ATaskEvents:
    """In-process subscribers for A2A task lifecycle updates."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[queue.Queue[JsonMap]]] = {}

    def publish(self, task_id: str, task: JsonMap) -> None:
        """Publish one task update to local subscribers."""
        event = {"task": copy.deepcopy(task)}
        for subscriber in list(self._subscribers.get(task_id, [])):
            subscriber.put(event)

    def subscribe(
        self,
        task_id: str,
        task: JsonMap,
        *,
        wait_seconds: float | None,
        default_wait_seconds: float,
    ) -> list[JsonMap]:
        """Return the initial task event plus queued updates for one subscription."""
        events = [{"task": copy.deepcopy(task)}]
        state = str(task.get("status", {}).get("state", ""))
        if state in TERMINAL_TASK_STATES:
            return events
        updates: queue.Queue[JsonMap] = queue.Queue()
        self._subscribers.setdefault(task_id, []).append(updates)
        timeout = default_wait_seconds if wait_seconds is None else max(wait_seconds, 0.0)
        try:
            if timeout > 0.0:
                try:
                    events.append(updates.get(timeout=timeout))
                except queue.Empty:
                    pass
        finally:
            subscribers = self._subscribers.get(task_id, [])
            if updates in subscribers:
                subscribers.remove(updates)
            if not subscribers and task_id in self._subscribers:
                del self._subscribers[task_id]
        return events
