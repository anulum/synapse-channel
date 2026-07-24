# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Agent2Agent bridge task events
"""Task-event fanout for the Agent2Agent bridge with optional durable history.

Live subscribers remain process-local queues. When a durable store is provided,
lifecycle events are also persisted so a restarted bridge can replay prior
snapshots to new subscribers of an existing task.
"""

from __future__ import annotations

import copy
import queue
import threading
from collections.abc import Iterable, Mapping
from typing import Protocol

from synapse_channel.a2a import JsonMap
from synapse_channel.a2a_validation import TERMINAL_TASK_STATES


class EventHistoryStore(Protocol):
    """Minimal durable event-history surface used by :class:`A2ATaskEvents`."""

    def append_event(self, task_id: str, event: JsonMap) -> None:
        """Persist one lifecycle event for ``task_id``."""

    def all_event_history(self) -> dict[str, list[JsonMap]]:
        """Return all persisted lifecycle event histories."""


class A2ATaskEvents:
    """Bounded subscribers for A2A task lifecycle updates.

    Parameters
    ----------
    max_history_events : int, optional
        Maximum in-memory events retained per task (default 64).
    durable_store : EventHistoryStore or None, optional
        When set, events are appended to durable storage and the in-memory
        history is seeded from that store at construction time.
    """

    def __init__(
        self,
        *,
        max_history_events: int = 64,
        durable_store: EventHistoryStore | None = None,
    ) -> None:
        self.max_history_events = max(max_history_events, 1)
        self._durable_store = durable_store
        self._subscribers: dict[str, list[queue.Queue[JsonMap]]] = {}
        self._history: dict[str, list[JsonMap]] = {}
        self._lock = threading.RLock()
        if durable_store is not None:
            self.seed_history(durable_store.all_event_history())

    def seed_history(self, history: Mapping[str, list[JsonMap]]) -> None:
        """Replace in-memory history from a durable snapshot (e.g. after restart)."""
        with self._lock:
            seeded: dict[str, list[JsonMap]] = {}
            for task_id, events in history.items():
                cleaned = [copy.deepcopy(event) for event in events if isinstance(event, dict)]
                if cleaned:
                    seeded[str(task_id)] = cleaned[-self.max_history_events :]
            self._history = seeded

    def publish(self, task_id: str, task: JsonMap) -> None:
        """Publish one task update to local subscribers and durable history."""
        event = self._event(task)
        with self._lock:
            history = self._history.setdefault(task_id, [])
            history.append(copy.deepcopy(event))
            del history[: -self.max_history_events]
            subscribers = list(self._subscribers.get(task_id, []))
        if self._durable_store is not None:
            self._durable_store.append_event(task_id, event)
        for subscriber in subscribers:
            subscriber.put(copy.deepcopy(event))

    def drop(self, task_ids: Iterable[str]) -> None:
        """Drop memory-only replay history and subscribers for removed tasks."""
        with self._lock:
            for task_id in task_ids:
                self._history.pop(task_id, None)
                self._subscribers.pop(task_id, None)

    def has_subscribers(self, task_id: str) -> bool:
        """Return whether any live local subscription is registered for a task.

        A subscription exists only between :meth:`subscribe` registering its
        queue and the same call draining it, so this is a point-in-time
        observation — useful for operational introspection and for
        deterministically sequencing a publish after a subscriber is known
        to be listening, instead of racing the registration.
        """
        with self._lock:
            return bool(self._subscribers.get(task_id))

    def history_for(self, task_id: str) -> list[JsonMap]:
        """Return a copy of the in-memory lifecycle history for ``task_id``."""
        with self._lock:
            return copy.deepcopy(self._history.get(task_id, []))

    def subscribe(
        self,
        task_id: str,
        task: JsonMap,
        *,
        wait_seconds: float | None,
        default_wait_seconds: float,
    ) -> list[JsonMap]:
        """Return bounded replay plus queued updates for one subscription."""
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
