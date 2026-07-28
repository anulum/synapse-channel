# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Agent2Agent bridge task storage
"""Task and push-configuration storage for the Agent2Agent bridge."""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from synapse_channel.a2a import JsonMap
from synapse_channel.a2a_errors import A2AQuotaError, A2AStoreError
from synapse_channel.a2a_push_delivery import PushDeliveryAttempt
from synapse_channel.a2a_validation import TERMINAL_TASK_STATES
from synapse_channel.core.numeric_coercion import safe_float

STALE_INFLIGHT_MESSAGE = "Recovered from stale in-flight task state after restart"
STATE_FILE_MODE = 0o600
DEFAULT_MAX_STORED_TASKS = 1024
DEFAULT_MAX_TASK_HISTORY = 64
DEFAULT_MAX_TASK_ARTIFACTS = 64
DEFAULT_MAX_PUSH_CONFIGS_PER_TASK = 16
DEFAULT_MAX_PUSH_DELIVERY_ATTEMPTS_PER_TASK = 256
DEFAULT_TASK_RETENTION_SECONDS = 7 * 24 * 60 * 60
StateWriter = Callable[[Path, str], None]


def _write_state(path: Path, payload: str) -> None:
    """Write serialized A2A state to ``path``."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, STATE_FILE_MODE)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    _restrict_state_file(path)


def _fsync_parent(path: Path) -> None:
    """Best-effort fsync for the parent directory containing ``path``."""
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    with suppress(OSError):
        fd = os.open(path.parent, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _restrict_state_file(path: Path) -> None:
    """Restrict an A2A state file path to owner-only access when supported."""
    with suppress(OSError):
        path.chmod(STATE_FILE_MODE)


class A2ATaskStore:
    """In-memory task view for one A2A bridge process."""

    def __init__(
        self,
        storage_path: str | Path | None = None,
        *,
        state_writer: StateWriter = _write_state,
        max_tasks: int = DEFAULT_MAX_STORED_TASKS,
        max_task_history: int = DEFAULT_MAX_TASK_HISTORY,
        max_task_artifacts: int = DEFAULT_MAX_TASK_ARTIFACTS,
        max_push_configs_per_task: int = DEFAULT_MAX_PUSH_CONFIGS_PER_TASK,
        max_push_delivery_attempts_per_task: int = DEFAULT_MAX_PUSH_DELIVERY_ATTEMPTS_PER_TASK,
        retention_seconds: float = DEFAULT_TASK_RETENTION_SECONDS,
    ) -> None:
        self._tasks: dict[str, JsonMap] = {}
        self._push_configs: dict[str, dict[str, JsonMap]] = {}
        self._push_delivery_attempts: dict[str, list[JsonMap]] = {}
        self._event_history: dict[str, list[JsonMap]] = {}
        self._storage_path = Path(storage_path) if storage_path is not None else None
        self._state_writer = state_writer
        self.max_tasks = max(max_tasks, 1)
        self.max_task_history = max(max_task_history, 0)
        self.max_task_artifacts = max(max_task_artifacts, 0)
        self.max_push_configs_per_task = max(max_push_configs_per_task, 0)
        self.max_push_delivery_attempts_per_task = max(max_push_delivery_attempts_per_task, 1)
        self.max_event_history = max(max_task_history, 1) if max_task_history else 64
        self.retention_seconds = max(retention_seconds, 0.0)
        self._lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        """Load persisted tasks, push configs, delivery evidence, and events."""
        if self._storage_path is None or not self._storage_path.exists():
            return
        try:
            data = json.loads(self._storage_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise A2AStoreError(f"Invalid A2A state file: {self._storage_path}") from exc
        tasks = data.get("tasks", {})
        push_configs = data.get("pushConfigs", {})
        push_delivery_attempts = data.get("pushDeliveryAttempts", {})
        event_history = data.get("eventHistory", {})
        if isinstance(tasks, dict):
            self._tasks = {
                str(task_id): self._recover_task(task)
                for task_id, task in tasks.items()
                if isinstance(task, dict)
            }
        if isinstance(push_configs, dict):
            self._push_configs = {
                str(task_id): {
                    str(config_id): config
                    for config_id, config in configs.items()
                    if isinstance(config, dict)
                }
                for task_id, configs in push_configs.items()
                if isinstance(configs, dict)
            }
        if isinstance(push_delivery_attempts, dict):
            self._push_delivery_attempts = {
                str(task_id): [dict(attempt) for attempt in attempts if isinstance(attempt, dict)][
                    -self.max_push_delivery_attempts_per_task :
                ]
                for task_id, attempts in push_delivery_attempts.items()
                if isinstance(attempts, list) and str(task_id) in self._tasks
            }
        if isinstance(event_history, dict):
            loaded: dict[str, list[JsonMap]] = {}
            for task_id, events in event_history.items():
                if not isinstance(events, list):
                    continue
                cleaned = [event for event in events if isinstance(event, dict)]
                if cleaned:
                    loaded[str(task_id)] = cleaned[-self.max_event_history :]
            self._event_history = loaded

    def _recover_task(self, task: JsonMap) -> JsonMap:
        """Return a safe restart view for one persisted task."""
        status = task.get("status")
        if not isinstance(status, dict):
            return task
        state = str(status.get("state", ""))
        if not state or state in TERMINAL_TASK_STATES:
            return task
        recovered = dict(task)
        recovered["status"] = {
            **status,
            "state": "TASK_STATE_FAILED",
            "message": STALE_INFLIGHT_MESSAGE,
        }
        return recovered

    def _normalize_task(self, task: JsonMap) -> JsonMap:
        """Apply bounded task history and artifact retention."""
        history = task.get("history")
        if isinstance(history, list):
            task["history"] = history[-self.max_task_history :] if self.max_task_history else []
        artifacts = task.get("artifacts")
        if isinstance(artifacts, list):
            task["artifacts"] = (
                artifacts[-self.max_task_artifacts :] if self.max_task_artifacts else []
            )
        return task

    def _task_updated_at(self, task: JsonMap) -> float:
        """Return the task timestamp used for retention ordering."""
        metadata = task.get("metadata")
        if not isinstance(metadata, dict):
            return 0.0
        # safe_float also absorbs OverflowError (a JSON integer too large for a
        # double) and rejects non-finite stamps, so retention ordering never sees
        # a NaN sort key.
        return safe_float(
            metadata.get("updatedAt") or metadata.get("createdAt") or 0.0, default=0.0
        )

    def _is_terminal_task(self, task: JsonMap) -> bool:
        """Return whether ``task`` is in a terminal A2A state."""
        status = task.get("status")
        return isinstance(status, dict) and status.get("state") in TERMINAL_TASK_STATES

    def _remove_task_locked(self, task_id: str) -> None:
        """Remove one task, its push configs, and durable events while locked."""
        self._tasks.pop(task_id, None)
        self._push_configs.pop(task_id, None)
        self._push_delivery_attempts.pop(task_id, None)
        self._event_history.pop(task_id, None)

    def _task_ids_by_age(self, *, terminal_only: bool) -> list[str]:
        """Return task ids ordered oldest first for quota eviction."""
        candidates = [
            task_id
            for task_id, task in self._tasks.items()
            if not terminal_only or self._is_terminal_task(task)
        ]
        return sorted(
            candidates, key=lambda task_id: (self._task_updated_at(self._tasks[task_id]), task_id)
        )

    def _enforce_task_limit_locked(self, *, protected_task_id: str) -> list[str]:
        """Drop old tasks until the store is within the configured task cap."""
        removed: list[str] = []
        while len(self._tasks) > self.max_tasks:
            terminal = [
                task_id
                for task_id in self._task_ids_by_age(terminal_only=True)
                if task_id != protected_task_id
            ]
            candidates = terminal or [
                task_id
                for task_id in self._task_ids_by_age(terminal_only=False)
                if task_id != protected_task_id
            ]
            task_id = candidates[0]
            self._remove_task_locked(task_id)
            removed.append(task_id)
        return removed

    def prune_expired(self, *, now: float) -> list[str]:
        """Remove expired terminal tasks and return their ids."""
        with self._lock:
            previous_tasks = dict(self._tasks)
            previous_push_configs = {
                task_id: dict(configs) for task_id, configs in self._push_configs.items()
            }
            previous_events = {
                task_id: [dict(item) for item in events]
                for task_id, events in self._event_history.items()
            }
            previous_push_delivery_attempts = {
                task_id: [dict(item) for item in attempts]
                for task_id, attempts in self._push_delivery_attempts.items()
            }
            removed = [
                task_id
                for task_id, task in self._tasks.items()
                if self._is_terminal_task(task)
                and now - self._task_updated_at(task) >= self.retention_seconds
            ]
            if not removed:
                return []
            for task_id in removed:
                self._remove_task_locked(task_id)
            try:
                self._save()
            except Exception:
                self._tasks = previous_tasks
                self._push_configs = previous_push_configs
                self._event_history = previous_events
                self._push_delivery_attempts = previous_push_delivery_attempts
                raise
            return sorted(removed)

    def _save(self) -> None:
        """Persist tasks, push configs, delivery evidence, and events."""
        if self._storage_path is None:
            return
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._storage_path.with_suffix(f"{self._storage_path.suffix}.tmp")
        payload = {
            "tasks": self._tasks,
            "pushConfigs": self._push_configs,
            "pushDeliveryAttempts": self._push_delivery_attempts,
            "eventHistory": self._event_history,
        }
        try:
            self._state_writer(tmp_path, json.dumps(payload, sort_keys=True))
            _restrict_state_file(tmp_path)
            tmp_path.replace(self._storage_path)
            _restrict_state_file(self._storage_path)
            _fsync_parent(self._storage_path)
        except Exception:
            if tmp_path.exists():
                _restrict_state_file(tmp_path)
            raise

    def put(self, task: JsonMap) -> JsonMap:
        """Store and return ``task``."""
        with self._lock:
            task_id = str(task["id"])
            previous_tasks = dict(self._tasks)
            previous_push_configs = {
                stored_id: dict(configs) for stored_id, configs in self._push_configs.items()
            }
            previous_events = {
                stored_id: [dict(item) for item in events]
                for stored_id, events in self._event_history.items()
            }
            previous_push_delivery_attempts = {
                stored_id: [dict(item) for item in attempts]
                for stored_id, attempts in self._push_delivery_attempts.items()
            }
            self._tasks[task_id] = self._normalize_task(task)
            self._enforce_task_limit_locked(protected_task_id=task_id)
            try:
                self._save()
            except Exception:
                self._tasks = previous_tasks
                self._push_configs = previous_push_configs
                self._event_history = previous_events
                self._push_delivery_attempts = previous_push_delivery_attempts
                raise
        return task

    def get(self, task_id: str) -> JsonMap | None:
        """Return one task by id, or ``None``."""
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self, *, state: str | None = None) -> list[JsonMap]:
        """Return tasks ordered by status-update timestamp descending.

        Ordering uses ``metadata.updatedAt`` (falling back to ``createdAt``),
        newest first. Task id is a stable ascending tie-break.

        Parameters
        ----------
        state : str or None, optional
            When set, only tasks whose status state equals ``state`` are returned.

        Returns
        -------
        list[dict[str, Any]]
            Filtered tasks in descending update-time order.
        """
        with self._lock:
            tasks = list(self._tasks.values())
        if state:
            tasks = [task for task in tasks if task.get("status", {}).get("state") == state]
        return sorted(
            tasks,
            key=lambda task: (-self._task_updated_at(task), str(task.get("id") or "")),
        )

    def append_event(self, task_id: str, event: JsonMap) -> None:
        """Append one durable lifecycle event for ``task_id`` and persist."""
        with self._lock:
            previous = {
                stored_id: [dict(item) for item in events]
                for stored_id, events in self._event_history.items()
            }
            history = self._event_history.setdefault(task_id, [])
            history.append(dict(event))
            del history[: -self.max_event_history]
            try:
                self._save()
            except Exception:
                self._event_history = previous
                raise

    def event_history(self, task_id: str) -> list[JsonMap]:
        """Return a copy of durable lifecycle events for ``task_id``."""
        with self._lock:
            return [dict(event) for event in self._event_history.get(task_id, [])]

    def all_event_history(self) -> dict[str, list[JsonMap]]:
        """Return a detached copy of all durable lifecycle event histories."""
        with self._lock:
            return {
                task_id: [dict(event) for event in events]
                for task_id, events in self._event_history.items()
            }

    def record_push_delivery_attempt(self, attempt: PushDeliveryAttempt) -> None:
        """Append one credential-free delivery attempt and persist it."""
        task_id = attempt.task_id
        with self._lock:
            if task_id not in self._tasks:
                raise A2AStoreError(f"Unknown task for push delivery evidence: {task_id}")
            previous = [dict(item) for item in self._push_delivery_attempts.get(task_id, [])]
            attempts = self._push_delivery_attempts.setdefault(task_id, [])
            attempts.append(attempt.to_json())
            del attempts[: -self.max_push_delivery_attempts_per_task]
            try:
                self._save()
            except Exception:
                if previous:
                    self._push_delivery_attempts[task_id] = previous
                else:
                    self._push_delivery_attempts.pop(task_id, None)
                raise

    def list_push_delivery_attempts(self, task_id: str) -> list[JsonMap]:
        """Return detached delivery evidence for ``task_id`` in attempt order."""
        with self._lock:
            return [dict(attempt) for attempt in self._push_delivery_attempts.get(task_id, [])]

    def put_push_config(self, task_id: str, config: JsonMap) -> JsonMap:
        """Store one push notification config for ``task_id``."""
        with self._lock:
            config_id = str(config.get("id") or uuid.uuid4())
            stored = dict(config)
            stored["id"] = config_id
            stored["taskId"] = task_id
            previous = dict(self._push_configs.get(task_id, {}))
            if config_id not in previous and len(previous) >= self.max_push_configs_per_task:
                raise A2AQuotaError("pushNotificationConfig limit exceeded")
            self._push_configs.setdefault(task_id, {})[config_id] = stored
            try:
                self._save()
            except Exception:
                if previous:
                    self._push_configs[task_id] = previous
                else:
                    self._push_configs.pop(task_id, None)
                raise
        return stored

    def get_push_config(self, task_id: str, config_id: str) -> JsonMap | None:
        """Return one push notification config."""
        with self._lock:
            return self._push_configs.get(task_id, {}).get(config_id)

    def list_push_configs(self, task_id: str) -> list[JsonMap]:
        """Return push notification configs for ``task_id`` sorted by id."""
        with self._lock:
            configs = list(self._push_configs.get(task_id, {}).values())
        return sorted(configs, key=lambda config: str(config["id"]))

    def delete_push_config(self, task_id: str, config_id: str) -> bool:
        """Delete one push notification config."""
        with self._lock:
            configs = self._push_configs.get(task_id)
            if not configs or config_id not in configs:
                return False
            previous = dict(configs)
            del configs[config_id]
            try:
                self._save()
            except Exception:
                self._push_configs[task_id] = previous
                raise
            return True
