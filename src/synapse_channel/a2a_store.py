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
import threading
import uuid
from pathlib import Path

from synapse_channel.a2a import JsonMap
from synapse_channel.a2a_validation import TERMINAL_TASK_STATES

STALE_INFLIGHT_MESSAGE = "Recovered from stale in-flight task state after restart"


class A2ATaskStore:
    """In-memory task view for one A2A bridge process."""

    def __init__(self, storage_path: str | Path | None = None) -> None:
        self._tasks: dict[str, JsonMap] = {}
        self._push_configs: dict[str, dict[str, JsonMap]] = {}
        self._storage_path = Path(storage_path) if storage_path is not None else None
        self._lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        """Load persisted tasks and push configs when a state file exists."""
        if self._storage_path is None or not self._storage_path.exists():
            return
        try:
            data = json.loads(self._storage_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid A2A state file: {self._storage_path}") from exc
        tasks = data.get("tasks", {})
        push_configs = data.get("pushConfigs", {})
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

    def _save(self) -> None:
        """Persist tasks and push configs to disk when configured."""
        if self._storage_path is None:
            return
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._storage_path.with_suffix(f"{self._storage_path.suffix}.tmp")
        payload = {
            "tasks": self._tasks,
            "pushConfigs": self._push_configs,
        }
        tmp_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self._storage_path)

    def put(self, task: JsonMap) -> JsonMap:
        """Store and return ``task``."""
        with self._lock:
            task_id = str(task["id"])
            previous = self._tasks.get(task_id)
            self._tasks[task_id] = task
            try:
                self._save()
            except Exception:
                if previous is None:
                    del self._tasks[task_id]
                else:
                    self._tasks[task_id] = previous
                raise
        return task

    def get(self, task_id: str) -> JsonMap | None:
        """Return one task by id, or ``None``."""
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self, *, state: str | None = None) -> list[JsonMap]:
        """Return tasks, optionally filtered by A2A status state."""
        with self._lock:
            tasks = list(self._tasks.values())
        if state:
            tasks = [task for task in tasks if task.get("status", {}).get("state") == state]
        return sorted(tasks, key=lambda task: str(task["id"]))

    def put_push_config(self, task_id: str, config: JsonMap) -> JsonMap:
        """Store one push notification config for ``task_id``."""
        with self._lock:
            config_id = str(config.get("id") or uuid.uuid4())
            stored = dict(config)
            stored["id"] = config_id
            stored["taskId"] = task_id
            previous = dict(self._push_configs.get(task_id, {}))
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
