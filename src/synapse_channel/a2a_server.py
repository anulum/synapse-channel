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
import threading
import time
import uuid
from collections.abc import Callable, Coroutine, Sequence
from typing import Any, cast

from synapse_channel import a2a_errors
from synapse_channel.a2a import JsonMap
from synapse_channel.a2a_events import A2ATaskEvents
from synapse_channel.a2a_http_protocol import (
    non_negative_int,
    normalise_authority,
    normalise_origin,
)
from synapse_channel.a2a_push import PushDeliverer, deliver_push_notification, http_push_deliverer
from synapse_channel.a2a_rpc import dispatch_json_rpc
from synapse_channel.a2a_store import A2ATaskStore
from synapse_channel.a2a_task_flow import (
    build_working_task,
    prepare_continuation,
    render_message_text,
    resolve_target,
    stored_task_target,
    user_status_message,
)
from synapse_channel.a2a_validation import (
    OPEN_TASK_STATES,
    TERMINAL_TASK_STATES,
    validate_bridge_id,
    validate_message_parts,
    validate_webhook_url,
)
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.numeric_coercion import safe_float

A2A_METADATA_TASK_ID = "a2aTaskId"
"""Chat metadata key carrying the bridge task id."""

A2A_METADATA_CONTEXT_ID = "a2aContextId"
"""Chat metadata key carrying the bridge context id."""


def _valid_metadata_id(value: object) -> str | None:
    """Return ``value`` as a bridge-safe id, or ``None`` when unusable."""
    if value is None:
        return None
    candidate = str(value)
    try:
        validate_bridge_id(candidate, field="metadata")
    except ValueError:
        return None
    return candidate


def _a2a_metadata_correlation(data: JsonMap) -> tuple[str | None, str | None, bool]:
    """Extract trusted A2A correlation fields from structured chat metadata.

    The boolean is ``True`` only when the frame attempted A2A correlation. A
    malformed attempt is rejected by the caller instead of falling back to text or
    sender inference, which keeps user-controlled chat content from selecting a
    task.
    """
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        return None, None, False
    if A2A_METADATA_TASK_ID not in metadata and A2A_METADATA_CONTEXT_ID not in metadata:
        return None, None, False
    return (
        _valid_metadata_id(metadata.get(A2A_METADATA_TASK_ID)),
        _valid_metadata_id(metadata.get(A2A_METADATA_CONTEXT_ID)),
        True,
    )


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
        """Cancel private-loop tasks, then stop and close the event loop."""
        self.agent.running = False
        if self.loop.is_closed():
            return
        if not self.loop.is_running():
            self.loop.close()
            return
        drained = asyncio.run_coroutine_threadsafe(self._cancel_pending_tasks(), self.loop)
        try:
            drained.result(timeout=2.0)
        except TimeoutError:
            drained.cancel()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join()
        self.loop.close()

    @staticmethod
    async def _cancel_pending_tasks() -> None:
        """Cancel and await every task owned by the private agent loop."""
        current = asyncio.current_task()
        pending = [task for task in asyncio.all_tasks() if task is not current]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


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
        allowed_origins: Sequence[str] = (),
        allowed_authorities: Sequence[str] = (),
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
        # Normalised once here so the per-request check is a plain membership
        # test and the HTTP edge never re-implements origin comparison rules.
        self.allowed_origins = tuple(normalise_origin(origin) for origin in allowed_origins)
        self.allowed_authorities = tuple(
            normalise_authority(authority) for authority in allowed_authorities
        )
        if self.allowed_origins and not self.allowed_authorities:
            raise ValueError("an Origin allow-list requires one trusted endpoint authority")
        self.task_timeout_seconds = max(task_timeout_seconds, 0.0)
        self.subscribe_wait_seconds = max(subscribe_wait_seconds, 0.0)
        self._pending_by_target: dict[str, list[str]] = {}
        self._events = A2ATaskEvents()
        self._task_creation_lock = threading.RLock()
        self._correlation_lock = threading.RLock()
        self._recover_stale_open_tasks(now=time.time())

    def _gc_retained_tasks(self, *, now: float | None = None) -> list[str]:
        """Prune expired terminal tasks and drop bridge-local indexes."""
        removed = self.store.prune_expired(now=time.time() if now is None else now)
        if not removed:
            return []
        for task_id in removed:
            self._remove_pending_task(task_id)
        self._events.drop(removed)
        return removed

    def _run(self, coro: Coroutine[Any, Any, Any]) -> Any:
        """Run ``coro`` through the configured submitter or a fresh event loop."""
        if self._submit is not None:
            return self._submit(coro)
        return asyncio.run(coro)

    def create_working_task(self, message: JsonMap, *, target: str | None = None) -> JsonMap:
        """Create a working A2A task and forward the request into SYNAPSE."""
        with self._task_creation_lock:
            self._gc_retained_tasks()
            raw_task_id = message.get("taskId")
            raw_context_id = message.get("contextId")
            validate_bridge_id(raw_task_id, field="taskId")
            validate_bridge_id(raw_context_id, field="contextId")
            task_id = str(raw_task_id or uuid.uuid4())
            context_id = str(raw_context_id or uuid.uuid4())
            if raw_task_id is not None and self.store.get(task_id) is not None:
                raise a2a_errors.A2AConflictError("message.taskId already exists")
            resolved_target = resolve_target(message, default=target or self.target)
            task = build_working_task(
                message,
                task_id=task_id,
                context_id=context_id,
                target=resolved_target,
                now=time.time(),
            )
            self._forward_message(
                message,
                task_id=task_id,
                context_id=context_id,
                target=resolved_target,
            )
            stored = self._set_task_status(
                task,
                state="TASK_STATE_WORKING",
                message=task["status"]["message"],
                publish=False,
            )
            self._publish_task_update(stored, deliver_push=False)
            return stored

    def create_completed_task(self, message: JsonMap, *, target: str | None = None) -> JsonMap:
        """Create a task for compatibility with older callers."""
        return self.create_working_task(message, target=target)

    def send_message(self, payload: JsonMap, *, protocol_version: str | None = None) -> JsonMap:
        """Handle an A2A ``message:send`` request."""
        task = self._send_message_task(payload, protocol_version=protocol_version)
        self._store_request_push_config(payload, task_id=str(task["id"]))
        return {"task": task}

    def stream_message(self, payload: JsonMap, *, protocol_version: str | None = None) -> JsonMap:
        """Handle an A2A ``message:stream`` request as an immediate lifecycle stream."""
        task = self._send_message_task(payload, protocol_version=protocol_version)
        self._store_request_push_config(payload, task_id=str(task["id"]))
        return {"task": task}

    def _send_message_task(
        self,
        payload: JsonMap,
        *,
        protocol_version: str | None = None,
    ) -> JsonMap:
        """Validate a send payload and return the created task."""
        with self._task_creation_lock:
            message = payload.get("message")
            if not isinstance(message, dict):
                raise a2a_errors.A2AValidationError("message must be an object")
            if not message.get("messageId"):
                raise a2a_errors.A2AValidationError("message.messageId is required")
            if message.get("role") != "ROLE_USER":
                raise a2a_errors.A2AValidationError("message.role must be ROLE_USER")
            validate_message_parts(message.get("parts"))
            validate_bridge_id(message.get("taskId"), field="taskId")
            validate_bridge_id(message.get("contextId"), field="contextId")
            task_id = message.get("taskId")
            existing = self.store.get(str(task_id)) if task_id is not None else None
            if protocol_version == "1.0" and task_id is not None:
                if existing is None:
                    raise a2a_errors.A2ANotFoundError(f"Unknown task: {task_id}")
                return self._continue_working_task(existing, message)
            if task_id is not None and existing is not None:
                raise a2a_errors.A2AConflictError("message.taskId already exists")
            return self.create_working_task(message)

    def _continue_working_task(self, task: JsonMap, message: JsonMap) -> JsonMap:
        """Continue a non-terminal task under A2A 1.0 task-id semantics."""
        status = task.get("status")
        if isinstance(status, dict) and status.get("state") in TERMINAL_TASK_STATES:
            raise a2a_errors.A2AConflictError("terminal task cannot accept another message")
        continued = prepare_continuation(task, message)
        task_id = str(task["id"])
        context_id = str(task["contextId"])
        target = stored_task_target(task, default=self.target)
        history = task.setdefault("history", [])
        if isinstance(history, list):
            history.append(continued)
        self._forward_message(
            continued,
            task_id=task_id,
            context_id=context_id,
            target=target,
        )
        return self._set_task_status(
            task,
            state="TASK_STATE_WORKING",
            message=user_status_message(continued),
        )

    def _forward_message(
        self,
        message: JsonMap,
        *,
        task_id: str,
        context_id: str,
        target: str,
    ) -> None:
        """Forward one task-bound message and maintain fallback correlation."""
        pending = self._pending_by_target.setdefault(target, [])
        if task_id not in pending:
            pending.append(task_id)
        text = render_message_text(message)
        if text:
            self._run(
                self.agent.chat(
                    text,
                    target=target,
                    metadata={
                        A2A_METADATA_TASK_ID: task_id,
                        A2A_METADATA_CONTEXT_ID: context_id,
                    },
                )
            )

    def _store_request_push_config(self, payload: JsonMap, *, task_id: str) -> JsonMap | None:
        """Store a send-time push notification config when one is present."""
        configuration = payload.get("configuration")
        if not isinstance(configuration, dict):
            return None
        task_config = configuration.get("taskPushNotificationConfig")
        if not isinstance(task_config, dict):
            return None
        if "pushNotificationConfig" in task_config:
            nested_config = task_config["pushNotificationConfig"]
            if not isinstance(nested_config, dict):
                return None
            config = nested_config
        else:
            config = task_config
        task = self.store.get(task_id)
        if task is None:
            return None
        stored = cast(JsonMap, self.create_push_notification_config(task_id, config))
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

            task_id, context_id, has_correlation_metadata = _a2a_metadata_correlation(data)
            if has_correlation_metadata and (task_id is None or context_id is None):
                return
            if task_id is None and sender in self._pending_by_target:
                task_id = self._pending_task_for_sender(sender)

            if not task_id:
                return
            task = self.store.get(task_id)
            if task is None or not self._sender_matches_task(task, sender):
                return
            if has_correlation_metadata and str(task.get("contextId", "")) != context_id:
                return
            status = task.get("status", {})
            if isinstance(status, dict) and status.get("state") in TERMINAL_TASK_STATES:
                return

            reply_text = payload.strip()
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
                # A hostile or malformed timestamp (mapping, huge int, NaN) must not
                # crash the sweep or park the task forever; unusable stamps read as
                # stale so the timeout still fires.
                updated_at = safe_float(
                    metadata.get("updatedAt") or metadata.get("createdAt") or 0.0,
                    default=0.0,
                )
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
        self._gc_retained_tasks()
        tasks = self.store.list_tasks(state=state)
        total = len(tasks)
        start = non_negative_int(page_token, default=0)
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
        self._gc_retained_tasks()
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
        status = task.get("status", {})
        if isinstance(status, dict) and status.get("state") in TERMINAL_TASK_STATES:
            return task
        self._remove_pending_task(task_id)
        return self._set_task_status(task, state="TASK_STATE_CANCELED")

    def subscribe_task(self, task_id: str) -> JsonMap | None:
        """Return a task snapshot for SSE subscription, or ``None`` when unknown."""
        self._gc_retained_tasks()
        return self.store.get(task_id)

    def subscribe_task_events(
        self,
        task_id: str,
        *,
        wait_seconds: float | None = None,
    ) -> list[JsonMap] | None:
        """Return bounded memory-only task events for one local subscription."""
        self._gc_retained_tasks()
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
        self._gc_retained_tasks()
        if self.store.get(task_id) is None:
            return None
        webhook = config.get("webhookUrl") or config.get("url")
        if not webhook:
            raise a2a_errors.A2AValidationError("pushNotificationConfig.webhookUrl is required")
        stored = dict(config)
        stored["webhookUrl"] = validate_webhook_url(webhook)
        return self.store.put_push_config(task_id, stored)

    def list_push_notification_configs(self, task_id: str) -> JsonMap:
        """Return all push notification configs for ``task_id``."""
        self._gc_retained_tasks()
        return {"pushNotificationConfigs": self.store.list_push_configs(task_id)}

    def get_push_notification_config(self, task_id: str, config_id: str) -> JsonMap | None:
        """Return one push notification config."""
        self._gc_retained_tasks()
        return self.store.get_push_config(task_id, config_id)

    def delete_push_notification_config(self, task_id: str, config_id: str) -> JsonMap:
        """Delete one push notification config and report whether it existed."""
        self._gc_retained_tasks()
        return {"deleted": self.store.delete_push_config(task_id, config_id)}

    def handle_json_rpc(
        self,
        request_body: JsonMap,
        *,
        protocol_version: str | None = None,
    ) -> JsonMap:
        """Dispatch one JSON-RPC 2.0 A2A request."""
        return dispatch_json_rpc(
            self,
            request_body,
            protocol_version=protocol_version,
        )
