# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Agent2Agent bridge orchestration
"""Agent2Agent bridge orchestration for SYNAPSE.

This module owns task creation, SYNAPSE correlation, JSON-RPC dispatch, lifecycle
state, timeout handling, subscriptions, and task-store coordination. The stdlib
HTTP edge and push delivery helpers live in focused sibling modules.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

from synapse_channel.a2a import JsonMap
from synapse_channel.a2a_events import A2ATaskEvents
from synapse_channel.a2a_push import PushDeliverer, deliver_push_notification, http_push_deliverer
from synapse_channel.a2a_store import A2ATaskStore
from synapse_channel.a2a_validation import (
    OPEN_TASK_STATES,
    TERMINAL_TASK_STATES,
    marker_context_id,
    marker_task_id,
    strip_task_marker,
    validate_bridge_id,
    validate_message_parts,
    validate_webhook_url,
)
from synapse_channel.client.agent import SynapseAgent


def _rpc_success(rpc_id: object, result: object) -> JsonMap:
    """Build a JSON-RPC success response."""
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _rpc_error(rpc_id: object, code: int, message: str) -> JsonMap:
    """Build a JSON-RPC error response."""
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


def _non_negative_int(value: object, *, default: int = 0) -> int:
    """Parse a non-negative integer from JSON or query input."""
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return default
    return max(parsed, 0)


class SynapseAgentRuntime:
    """Background event-loop owner for a live ``SynapseAgent`` connection."""

    def __init__(self, agent: SynapseAgent) -> None:
        self.agent = agent
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="synapse-a2a", daemon=True)

    def _run_loop(self) -> None:
        """Run the bridge event loop forever."""
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def start(self, *, ready_timeout: float = 5.0) -> bool:
        """Start the agent connection and wait for the hub welcome."""
        self._thread.start()
        asyncio.run_coroutine_threadsafe(self.agent.connect(), self.loop)
        ready = asyncio.run_coroutine_threadsafe(
            self.agent.wait_until_ready(timeout=ready_timeout), self.loop
        )
        return bool(ready.result(timeout=max(ready_timeout + 1.0, 1.0)))

    def run(self, coro: Coroutine[Any, Any, Any]) -> Any:
        """Run one coroutine on the agent loop and return its result."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()

    def stop(self) -> None:
        """Stop the agent connection and event loop."""
        self.agent.running = False
        self.loop.call_soon_threadsafe(self.loop.stop)


class A2ABridge:
    """Translate A2A HTTP operations into SYNAPSE operations."""

    def __init__(
        self,
        *,
        agent: Any,
        agent_card: JsonMap,
        target: str,
        store: A2ATaskStore | None = None,
        submit: Callable[[Coroutine[Any, Any, Any]], Any] | None = None,
        push_deliverer: PushDeliverer | None = None,
        auth_token: str | None = None,
        task_timeout_seconds: float = 300.0,
        subscribe_wait_seconds: float = 0.0,
    ) -> None:
        self.agent = agent
        self.agent_card = agent_card
        self.target = target
        self.store = store or A2ATaskStore()
        self._submit = submit
        self._push_deliverer = push_deliverer or http_push_deliverer
        self.auth_token = auth_token
        self.task_timeout_seconds = max(task_timeout_seconds, 0.0)
        self.subscribe_wait_seconds = max(subscribe_wait_seconds, 0.0)
        self._pending_by_target: dict[str, list[str]] = {}
        self._events = A2ATaskEvents()
        self._task_creation_lock = threading.RLock()
        self._correlation_lock = threading.RLock()
        self._recover_stale_open_tasks(now=time.time())

    def _run(self, coro: Coroutine[Any, Any, Any]) -> Any:
        """Run ``coro`` through the configured submitter or a fresh event loop."""
        if self._submit is not None:
            return self._submit(coro)
        return asyncio.run(coro)

    def _message_text(self, message: JsonMap) -> str:
        """Render A2A message parts into the text sent over SYNAPSE."""
        rendered: list[str] = []
        for part in message.get("parts", []):
            if not isinstance(part, dict):
                continue
            if "text" in part:
                rendered.append(str(part["text"]))
            elif "data" in part:
                rendered.append(json.dumps(part["data"], sort_keys=True))
            elif "url" in part:
                rendered.append(str(part["url"]))
            elif isinstance(part.get("file"), dict):
                file_part = part["file"]
                file_bits = [
                    str(file_part[value])
                    for value in ("name", "mimeType", "uri")
                    if file_part.get(value)
                ]
                if file_bits:
                    rendered.append(f"[file: {'; '.join(file_bits)}]")
            elif "raw" in part:
                rendered.append("[raw omitted]")
        return "\n".join(text for text in rendered if text).strip()

    def _target_for(self, message: JsonMap, fallback: str | None = None) -> str:
        """Resolve the SYNAPSE target for one A2A message."""
        metadata = message.get("metadata")
        if isinstance(metadata, dict):
            target = metadata.get("target") or metadata.get("synapseTarget")
            if target:
                return str(target)
        return fallback or self.target

    def create_working_task(self, message: JsonMap, *, target: str | None = None) -> JsonMap:
        """Create a working A2A task and forward the request into SYNAPSE."""
        with self._task_creation_lock:
            raw_task_id = message.get("taskId")
            raw_context_id = message.get("contextId")
            validate_bridge_id(raw_task_id, field="taskId")
            validate_bridge_id(raw_context_id, field="contextId")
            task_id = str(raw_task_id or uuid.uuid4())
            context_id = str(raw_context_id or uuid.uuid4())
            if raw_task_id is not None and self.store.get(task_id) is not None:
                raise ValueError("message.taskId already exists")
            resolved_target = self._target_for(message, target)
            text = self._message_text(message)
            now = time.time()
            task: JsonMap = {
                "id": task_id,
                "contextId": context_id,
                "status": {
                    "state": "TASK_STATE_SUBMITTED",
                    "message": {
                        "messageId": str(uuid.uuid4()),
                        "role": "ROLE_USER",
                        "parts": message.get("parts", []),
                    },
                },
                "history": [message],
                "artifacts": [],
                "metadata": {
                    "synapseTarget": resolved_target,
                    "a2aTaskId": task_id,
                    "a2aContextId": context_id,
                    "createdAt": now,
                    "updatedAt": now,
                },
            }
            self._pending_by_target.setdefault(resolved_target, []).append(task_id)
            if text:
                marked = text + f"\n[A2A-TASK:{task_id} contextId={context_id}]"
                self._run(self.agent.chat(marked, target=resolved_target))
            task = self._set_task_status(
                task,
                state="TASK_STATE_WORKING",
                message=task["status"]["message"],
                publish=False,
            )
            stored = self.store.put(task)
            self._publish_task_update(stored, deliver_push=False)
            return stored

    def create_completed_task(self, message: JsonMap, *, target: str | None = None) -> JsonMap:
        """Create a task for compatibility with older callers."""
        return self.create_working_task(message, target=target)

    def send_message(self, payload: JsonMap) -> JsonMap:
        """Handle an A2A ``message:send`` request."""
        task = self._send_message_task(payload)
        self._store_request_push_config(payload, task_id=str(task["id"]))
        return {"task": task}

    def stream_message(self, payload: JsonMap) -> JsonMap:
        """Handle an A2A ``message:stream`` request as an immediate lifecycle stream."""
        task = self._send_message_task(payload)
        self._store_request_push_config(payload, task_id=str(task["id"]))
        return {"task": task}

    def _send_message_task(self, payload: JsonMap) -> JsonMap:
        """Validate a send payload and return the created task."""
        with self._task_creation_lock:
            message = payload.get("message")
            if not isinstance(message, dict):
                raise ValueError("message must be an object")
            if not message.get("messageId"):
                raise ValueError("message.messageId is required")
            if message.get("role") != "ROLE_USER":
                raise ValueError("message.role must be ROLE_USER")
            validate_message_parts(message.get("parts"))
            validate_bridge_id(message.get("taskId"), field="taskId")
            validate_bridge_id(message.get("contextId"), field="contextId")
            task_id = message.get("taskId")
            if task_id is not None and self.store.get(str(task_id)) is not None:
                raise ValueError("message.taskId already exists")
            return self.create_working_task(message)

    def _store_request_push_config(self, payload: JsonMap, *, task_id: str) -> JsonMap | None:
        """Store a send-time push notification config when one is present."""
        configuration = payload.get("configuration")
        if not isinstance(configuration, dict):
            return None
        task_config = configuration.get("taskPushNotificationConfig")
        if not isinstance(task_config, dict):
            return None
        config = task_config.get("pushNotificationConfig")
        if not isinstance(config, dict):
            return None
        task = self.store.get(task_id)
        stored = self.create_push_notification_config(task_id, config)
        if stored is None:
            return None
        assert task is not None
        self._deliver_push_notification(task=task, config=stored)
        return stored

    def _deliver_push_notification(self, *, task: JsonMap, config: JsonMap) -> None:
        """Deliver one task update to a configured push-notification webhook."""
        deliver_push_notification(task=task, config=config, push_deliverer=self._push_deliverer)

    def _deliver_push_notifications(self, task: JsonMap) -> None:
        """Deliver one task update to every stored push config for the task."""
        for config in self.store.list_push_configs(str(task["id"])):
            self._deliver_push_notification(task=task, config=config)

    def _set_task_status(
        self,
        task: JsonMap,
        *,
        state: str,
        message: JsonMap | None = None,
        publish: bool = True,
    ) -> JsonMap:
        """Set task status and refresh bridge-local lifecycle metadata."""
        status: JsonMap = {"state": state}
        if message is not None:
            status["message"] = message
        task["status"] = status
        metadata = task.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata["updatedAt"] = time.time()
        stored = self.store.put(task)
        if publish:
            self._publish_task_update(stored)
        return stored

    def _publish_task_update(self, task: JsonMap, *, deliver_push: bool = True) -> None:
        """Publish one task update to local subscribers and configured webhooks."""
        self._events.publish(str(task["id"]), task)
        if deliver_push:
            self._deliver_push_notifications(task)

    def _remove_pending_task(self, task_id: str) -> None:
        """Remove ``task_id`` from all pending fallback correlation lists."""
        for target, pending in list(self._pending_by_target.items()):
            self._pending_by_target[target] = [stored for stored in pending if stored != task_id]
            if not self._pending_by_target[target]:
                del self._pending_by_target[target]

    def _pending_task_for_sender(self, sender: str) -> str | None:
        """Return the oldest pending task for one SYNAPSE sender."""
        pending = self._pending_by_target.get(sender)
        if not pending:
            return None
        while pending:
            task_id = pending.pop(0)
            if self.store.get(task_id) is not None:
                return task_id
        del self._pending_by_target[sender]
        return None

    def _sender_matches_task(self, task: JsonMap, sender: str) -> bool:
        """Return whether ``sender`` is allowed to complete ``task``."""
        metadata = task.get("metadata")
        target = ""
        if isinstance(metadata, dict):
            target = str(metadata.get("synapseTarget") or "")
        return target in {"", "all", sender}

    def handle_synapse_frame(self, data: JsonMap) -> None:
        """Correlate an inbound SYNAPSE chat frame to an open A2A task."""
        with self._correlation_lock:
            if data.get("type") != "chat":
                return
            payload = str(data.get("payload", ""))
            sender = str(data.get("sender", ""))

            task_id = marker_task_id(payload)
            has_marker = task_id is not None
            if task_id is None and sender in self._pending_by_target:
                task_id = self._pending_task_for_sender(sender)

            if not task_id:
                return
            task = self.store.get(task_id)
            if task is None or not self._sender_matches_task(task, sender):
                return
            context_id = marker_context_id(payload)
            if has_marker and context_id is None:
                return
            if context_id is not None and str(task.get("contextId", "")) != context_id:
                return
            status = task.get("status", {})
            if isinstance(status, dict) and status.get("state") in TERMINAL_TASK_STATES:
                return

            reply_text = strip_task_marker(payload)
            reply_part: JsonMap = {
                "messageId": str(uuid.uuid4()),
                "role": "ROLE_AGENT",
                "parts": [{"text": reply_text, "mediaType": "text/plain"}],
            }
            task.setdefault("history", []).append(reply_part)
            task.setdefault("artifacts", []).append(
                {
                    "artifactId": f"synapse-reply-{task_id}",
                    "name": "SYNAPSE reply",
                    "description": f"Correlated reply from {sender}",
                    "parts": [{"text": reply_text, "mediaType": "text/plain"}],
                }
            )
            self._remove_pending_task(task_id)
            self._set_task_status(task, state="TASK_STATE_COMPLETED", message=reply_part)

    def expire_timed_out_tasks(self, *, now: float | None = None) -> list[JsonMap]:
        """Fail open tasks whose reply deadline has elapsed."""
        return self._fail_stale_open_tasks(
            now=time.time() if now is None else now,
            detail="Timed out waiting for correlated SYNAPSE reply.",
        )

    def _recover_stale_open_tasks(self, *, now: float) -> list[JsonMap]:
        """Fail persisted open tasks that were stale before bridge startup."""
        return self._fail_stale_open_tasks(
            now=now,
            detail="Recovered stale A2A task from persisted bridge state.",
        )

    def _fail_stale_open_tasks(self, *, now: float, detail: str) -> list[JsonMap]:
        """Fail open tasks older than the configured timeout."""
        if self.task_timeout_seconds <= 0.0:
            return []
        failed: list[JsonMap] = []
        for task in self.store.list_tasks():
            status = task.get("status", {})
            if not isinstance(status, dict) or status.get("state") not in OPEN_TASK_STATES:
                continue
            metadata = task.get("metadata", {})
            updated_at = 0.0
            if isinstance(metadata, dict):
                updated_at = float(metadata.get("updatedAt") or metadata.get("createdAt") or 0.0)
            if now - updated_at < self.task_timeout_seconds:
                continue
            task_id = str(task["id"])
            message = {
                "messageId": str(uuid.uuid4()),
                "role": "ROLE_AGENT",
                "parts": [{"text": detail, "mediaType": "text/plain"}],
            }
            self._remove_pending_task(task_id)
            failed.append(self._set_task_status(task, state="TASK_STATE_FAILED", message=message))
        return failed

    def list_tasks(
        self,
        *,
        state: str | None = None,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> JsonMap:
        """Return an A2A task-list response."""
        tasks = self.store.list_tasks(state=state)
        total = len(tasks)
        start = _non_negative_int(page_token, default=0)
        if page_size is None:
            page_size = total
        page_size = max(page_size, 0)
        end = start + page_size
        page = tasks[start:end]
        next_page_token = str(end) if end < total else ""
        return {
            "tasks": page,
            "nextPageToken": next_page_token,
            "pageSize": len(page),
            "totalSize": total,
        }

    def get_task(self, task_id: str, *, history_length: int | None = None) -> JsonMap | None:
        """Return one A2A task by id."""
        task = self.store.get(task_id)
        if task is None or history_length is None:
            return task
        trimmed = dict(task)
        history = task.get("history")
        if isinstance(history, list):
            trimmed["history"] = history[-history_length:] if history_length > 0 else []
        return trimmed

    def cancel_task(self, task_id: str) -> JsonMap | None:
        """Cancel a stored A2A task."""
        task = self.store.get(task_id)
        if task is None:
            return None
        self._remove_pending_task(task_id)
        return self._set_task_status(task, state="TASK_STATE_CANCELED")

    def subscribe_task(self, task_id: str) -> JsonMap | None:
        """Return a task snapshot for SSE subscription, or ``None`` when unknown."""
        return self.store.get(task_id)

    def subscribe_task_events(
        self,
        task_id: str,
        *,
        wait_seconds: float | None = None,
    ) -> list[JsonMap] | None:
        """Return the initial task event plus queued updates for one subscription."""
        task = self.store.get(task_id)
        if task is None:
            return None
        return self._events.subscribe(
            task_id,
            task,
            wait_seconds=wait_seconds,
            default_wait_seconds=self.subscribe_wait_seconds,
        )

    def create_push_notification_config(self, task_id: str, config: JsonMap) -> JsonMap | None:
        """Create a push notification config for a known task."""
        if self.store.get(task_id) is None:
            return None
        webhook = config.get("webhookUrl") or config.get("url")
        if not webhook:
            raise ValueError("pushNotificationConfig.webhookUrl is required")
        stored = dict(config)
        stored["webhookUrl"] = validate_webhook_url(webhook)
        return self.store.put_push_config(task_id, stored)

    def list_push_notification_configs(self, task_id: str) -> JsonMap:
        """Return all push notification configs for ``task_id``."""
        return {"pushNotificationConfigs": self.store.list_push_configs(task_id)}

    def get_push_notification_config(self, task_id: str, config_id: str) -> JsonMap | None:
        """Return one push notification config."""
        return self.store.get_push_config(task_id, config_id)

    def delete_push_notification_config(self, task_id: str, config_id: str) -> JsonMap:
        """Delete one push notification config and report whether it existed."""
        return {"deleted": self.store.delete_push_config(task_id, config_id)}

    def handle_json_rpc(self, request_body: JsonMap) -> JsonMap:
        """Dispatch one JSON-RPC 2.0 A2A request."""
        rpc_id = request_body.get("id")
        if request_body.get("jsonrpc") != "2.0" or not isinstance(request_body.get("method"), str):
            return _rpc_error(rpc_id, -32600, "Invalid Request")
        params = request_body.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _rpc_error(rpc_id, -32602, "Invalid params")
        method = str(request_body["method"])
        try:
            result = self._json_rpc_result(method, params)
        except KeyError:
            return _rpc_error(rpc_id, -32601, "Method not found")
        except ValueError as exc:
            return _rpc_error(rpc_id, -32602, str(exc))
        return _rpc_success(rpc_id, result)

    def _json_rpc_result(self, method: str, params: JsonMap) -> object:
        """Return the result object for one supported JSON-RPC method."""
        if method == "message/send":
            return self.send_message(params)
        if method == "message/stream":
            return self.stream_message(params)
        if method == "tasks/get":
            task_id = str(params.get("id") or params.get("taskId") or "")
            history_length = params.get("historyLength")
            task = self.get_task(
                task_id,
                history_length=(
                    _non_negative_int(history_length) if history_length is not None else None
                ),
            )
            if task is None:
                raise ValueError(f"Unknown task: {task_id}")
            return task
        if method == "tasks/list":
            state = params.get("status")
            page_size = params.get("pageSize")
            return self.list_tasks(
                state=str(state) if state else None,
                page_size=_non_negative_int(page_size) if page_size is not None else None,
                page_token=str(params.get("pageToken") or ""),
            )
        if method == "tasks/cancel":
            task_id = str(params.get("id") or params.get("taskId") or "")
            task = self.cancel_task(task_id)
            if task is None:
                raise ValueError(f"Unknown task: {task_id}")
            return task
        if method == "tasks/pushNotificationConfig/set":
            task_id = str(params.get("taskId") or params.get("id") or "")
            config = params.get("pushNotificationConfig")
            if not isinstance(config, dict):
                raise ValueError("pushNotificationConfig is required")
            created = self.create_push_notification_config(task_id, config)
            if created is None:
                raise ValueError(f"Unknown task: {task_id}")
            return created
        if method == "tasks/pushNotificationConfig/list":
            task_id = str(params.get("taskId") or params.get("id") or "")
            return self.list_push_notification_configs(task_id)["pushNotificationConfigs"]
        if method == "tasks/pushNotificationConfig/get":
            task_id = str(params.get("taskId") or params.get("id") or "")
            config_id = str(params.get("pushNotificationConfigId") or params.get("configId") or "")
            config = self.get_push_notification_config(task_id, config_id)
            if config is None:
                raise ValueError(f"Unknown push notification config: {config_id}")
            return config
        if method == "tasks/pushNotificationConfig/delete":
            task_id = str(params.get("taskId") or params.get("id") or "")
            config_id = str(params.get("pushNotificationConfigId") or params.get("configId") or "")
            return self.delete_push_notification_config(task_id, config_id)
        if method == "agent/getAuthenticatedExtendedCard":
            return self.agent_card
        raise KeyError(method)
