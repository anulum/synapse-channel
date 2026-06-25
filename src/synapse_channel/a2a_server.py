# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — stdlib HTTP+JSON Agent2Agent bridge
"""HTTP+JSON Agent2Agent bridge for SYNAPSE.

This module keeps A2A at the edge: the SYNAPSE hub stays WebSocket-native and
dependency-light, while a separate stdlib HTTP server exposes the A2A discovery,
message, and task routes.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from collections.abc import Callable, Coroutine
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib import request
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse

from synapse_channel.a2a import JsonMap
from synapse_channel.a2a_events import A2ATaskEvents
from synapse_channel.a2a_store import A2ATaskStore
from synapse_channel.a2a_validation import (
    A2A_MEDIA_TYPE,
    OPEN_TASK_STATES,
    PROBLEM_MEDIA_TYPE,
    SSE_MEDIA_TYPE,
    TERMINAL_TASK_STATES,
    is_supported_json_media_type,
    marker_context_id,
    marker_task_id,
    strip_task_marker,
    validate_bridge_id,
    validate_message_parts,
    validate_webhook_url,
)
from synapse_channel.client.agent import SynapseAgent

PushDeliverer = Callable[[JsonMap], None]
MAX_A2A_JSON_BODY_BYTES = 1024 * 1024


def _http_push_deliverer(delivery: JsonMap) -> None:
    """Deliver one push notification over stdlib HTTP."""
    raw = json.dumps(delivery["payload"], sort_keys=True).encode("utf-8")
    headers = {
        "Content-Type": A2A_MEDIA_TYPE,
        **delivery.get("headers", {}),
    }
    req = request.Request(
        str(delivery["url"]),
        data=raw,
        headers=headers,
        method="POST",
    )
    with request.urlopen(req, timeout=5.0) as response:
        response.read()


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


def _push_config_path(path: str) -> tuple[str, str | None] | None:
    """Parse ``/tasks/{task_id}/pushNotificationConfigs[/config_id]`` paths."""
    prefix = "/tasks/"
    marker = "/pushNotificationConfigs"
    if not path.startswith(prefix) or marker not in path:
        return None
    rest = path.removeprefix(prefix)
    task_id, _, tail = rest.partition(marker)
    if not task_id:
        return None
    config_id = tail.strip("/") or None
    return task_id, config_id


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
        self._push_deliverer = push_deliverer or _http_push_deliverer
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
            task_id = str(message.get("taskId") or uuid.uuid4())
            context_id = str(message.get("contextId") or uuid.uuid4())
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
        if stored is not None and task is not None:
            self._deliver_push_notification(task=task, config=stored)
        return stored

    def _deliver_push_notification(self, *, task: JsonMap, config: JsonMap) -> None:
        """Deliver one task update to a configured push-notification webhook."""
        headers: dict[str, str] = {}
        authentication = config.get("authentication")
        if isinstance(authentication, dict):
            scheme = str(authentication.get("scheme") or "").strip()
            credentials = str(authentication.get("credentials") or "").strip()
            if scheme and credentials:
                headers["Authorization"] = f"{scheme} {credentials}"
        try:
            self._push_deliverer(
                {
                    "url": str(config["webhookUrl"]),
                    "headers": headers,
                    "payload": {"task": task},
                }
            )
        except (OSError, TimeoutError, URLError):
            return

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


def _problem(status: HTTPStatus, title: str, detail: str = "") -> JsonMap:
    """Build an RFC 7807-style problem body."""
    body: JsonMap = {
        "type": "about:blank",
        "title": title,
        "status": int(status),
    }
    if detail:
        body["detail"] = detail
    return body


def build_a2a_handler(bridge: A2ABridge) -> type[BaseHTTPRequestHandler]:
    """Build a request-handler class bound to ``bridge``."""

    class A2ARequestHandler(BaseHTTPRequestHandler):
        """HTTP handler for one A2A bridge."""

        bridge: A2ABridge

        def log_message(self, _format: str, *_args: Any) -> None:
            """Silence stdlib access logging; the caller owns process logging."""
            return None

        def _send_json(
            self,
            status: HTTPStatus,
            body: JsonMap,
            *,
            media_type: str = A2A_MEDIA_TYPE,
        ) -> None:
            raw = json.dumps(body, sort_keys=True).encode("utf-8")
            self.send_response(int(status))
            self.send_header("Content-Type", media_type)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _send_sse(self, status: HTTPStatus, body: JsonMap) -> None:
            raw = f"data: {json.dumps(body, sort_keys=True)}\n\n".encode()
            self.send_response(int(status))
            self.send_header("Content-Type", SSE_MEDIA_TYPE)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _read_json(self) -> JsonMap | None:
            content_type = self.headers.get("Content-Type", "")
            if not is_supported_json_media_type(content_type):
                self._send_json(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    _problem(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Unsupported Media Type"),
                    media_type=PROBLEM_MEDIA_TYPE,
                )
                return None
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length > MAX_A2A_JSON_BODY_BYTES:
                self._send_json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    _problem(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body too large"),
                    media_type=PROBLEM_MEDIA_TYPE,
                )
                return None
            raw = self.rfile.read(max(length, 0))
            try:
                data = json.loads(raw.decode("utf-8") if raw else "{}")
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    _problem(HTTPStatus.BAD_REQUEST, "Invalid JSON"),
                    media_type=PROBLEM_MEDIA_TYPE,
                )
                return None
            if not isinstance(data, dict):
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    _problem(HTTPStatus.BAD_REQUEST, "Invalid request body"),
                    media_type=PROBLEM_MEDIA_TYPE,
                )
                return None
            return data

        def _send_not_found(self, detail: str = "") -> None:
            self._send_json(
                HTTPStatus.NOT_FOUND,
                _problem(HTTPStatus.NOT_FOUND, "Not Found", detail),
                media_type=PROBLEM_MEDIA_TYPE,
            )

        def _is_authorized(self) -> bool:
            token = self.bridge.auth_token
            if not token:
                return True
            return self.headers.get("Authorization") == f"Bearer {token}"

        def _require_authorized(self) -> bool:
            if self._is_authorized():
                return True
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                _problem(HTTPStatus.UNAUTHORIZED, "Unauthorized"),
                media_type=PROBLEM_MEDIA_TYPE,
            )
            return False

        def do_GET(self) -> None:
            """Serve A2A discovery and task-read endpoints."""
            parsed = urlparse(self.path)
            if parsed.path == "/.well-known/agent-card.json":
                self._send_json(HTTPStatus.OK, self.bridge.agent_card)
                return
            if not self._require_authorized():
                return
            if parsed.path == "/extendedAgentCard":
                self._send_json(HTTPStatus.OK, self.bridge.agent_card)
                return
            push_path = _push_config_path(parsed.path)
            if push_path is not None:
                task_id, config_id = push_path
                if self.bridge.get_task(task_id) is None:
                    self._send_not_found(f"Unknown task: {task_id}")
                    return
                if config_id is None:
                    configs = self.bridge.list_push_notification_configs(task_id)
                    self._send_json(HTTPStatus.OK, configs)
                    return
                config = self.bridge.get_push_notification_config(task_id, config_id)
                if config is None:
                    self._send_not_found(f"Unknown push notification config: {config_id}")
                    return
                self._send_json(HTTPStatus.OK, config)
                return
            if parsed.path == "/tasks":
                query = parse_qs(parsed.query)
                state = query.get("status", [None])[0]
                page_size = query.get("pageSize", [None])[0]
                page_token = query.get("pageToken", [""])[0]
                self._send_json(
                    HTTPStatus.OK,
                    self.bridge.list_tasks(
                        state=state,
                        page_size=(_non_negative_int(page_size) if page_size is not None else None),
                        page_token=page_token,
                    ),
                )
                return
            if parsed.path.startswith("/tasks/"):
                task_id = parsed.path.removeprefix("/tasks/")
                if ":" in task_id:
                    self._send_not_found()
                    return
                query = parse_qs(parsed.query)
                history_length = query.get("historyLength", [None])[0]
                task = self.bridge.get_task(
                    task_id,
                    history_length=(
                        _non_negative_int(history_length) if history_length is not None else None
                    ),
                )
                if task is None:
                    self._send_not_found(f"Unknown task: {task_id}")
                    return
                self._send_json(HTTPStatus.OK, task)
                return
            self._send_not_found()

        def do_POST(self) -> None:
            """Serve A2A message-send and task-cancel endpoints."""
            parsed = urlparse(self.path)
            if not self._require_authorized():
                return
            push_path = _push_config_path(parsed.path)
            if push_path is not None:
                task_id, config_id = push_path
                if config_id is not None:
                    self._send_not_found()
                    return
                data = self._read_json()
                if data is None:
                    return
                config = data.get("pushNotificationConfig", data)
                if not isinstance(config, dict):
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        _problem(HTTPStatus.BAD_REQUEST, "Invalid push notification config"),
                        media_type=PROBLEM_MEDIA_TYPE,
                    )
                    return
                try:
                    created = self.bridge.create_push_notification_config(task_id, config)
                except ValueError as exc:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        _problem(
                            HTTPStatus.BAD_REQUEST,
                            "Invalid push notification config",
                            str(exc),
                        ),
                        media_type=PROBLEM_MEDIA_TYPE,
                    )
                    return
                if created is None:
                    self._send_not_found(f"Unknown task: {task_id}")
                    return
                self._send_json(HTTPStatus.OK, created)
                return
            if parsed.path == "/message:stream":
                data = self._read_json()
                if data is None:
                    return
                try:
                    self._send_sse(HTTPStatus.OK, self.bridge.stream_message(data))
                except ValueError as exc:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        _problem(HTTPStatus.BAD_REQUEST, "Invalid A2A message", str(exc)),
                        media_type=PROBLEM_MEDIA_TYPE,
                    )
                return
            if parsed.path == "/message:send":
                data = self._read_json()
                if data is None:
                    return
                try:
                    self._send_json(HTTPStatus.OK, self.bridge.send_message(data))
                except ValueError as exc:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        _problem(HTTPStatus.BAD_REQUEST, "Invalid A2A message", str(exc)),
                        media_type=PROBLEM_MEDIA_TYPE,
                    )
                return
            if parsed.path in {"/", "/rpc"}:
                data = self._read_json()
                if data is None:
                    return
                self._send_json(HTTPStatus.OK, self.bridge.handle_json_rpc(data))
                return
            if parsed.path.startswith("/tasks/") and parsed.path.endswith(":cancel"):
                task_id = parsed.path.removeprefix("/tasks/").removesuffix(":cancel")
                task = self.bridge.cancel_task(task_id)
                if task is None:
                    self._send_not_found(f"Unknown task: {task_id}")
                    return
                self._send_json(HTTPStatus.OK, task)
                return
            if parsed.path.startswith("/tasks/") and parsed.path.endswith(":subscribe"):
                task_id = parsed.path.removeprefix("/tasks/").removesuffix(":subscribe")
                events = self.bridge.subscribe_task_events(task_id)
                if events is None:
                    self._send_not_found(f"Unknown task: {task_id}")
                    return
                task = events[0]["task"]
                state = str(task.get("status", {}).get("state", ""))
                if state in TERMINAL_TASK_STATES:
                    self._send_json(
                        HTTPStatus.CONFLICT,
                        _problem(
                            HTTPStatus.CONFLICT,
                            "Task is terminal",
                            "Terminal tasks cannot be subscribed to.",
                        ),
                        media_type=PROBLEM_MEDIA_TYPE,
                    )
                    return
                self._send_sse(HTTPStatus.OK, events[-1])
                return
            self._send_not_found()

        def do_DELETE(self) -> None:
            """Serve A2A push-notification config deletion."""
            parsed = urlparse(self.path)
            if not self._require_authorized():
                return
            push_path = _push_config_path(parsed.path)
            if push_path is None:
                self._send_not_found()
                return
            task_id, config_id = push_path
            if self.bridge.get_task(task_id) is None:
                self._send_not_found(f"Unknown task: {task_id}")
                return
            if config_id is None:
                self._send_not_found("Missing push notification config id.")
                return
            deleted = self.bridge.delete_push_notification_config(task_id, config_id)
            self._send_json(HTTPStatus.OK, deleted)

    A2ARequestHandler.bridge = bridge
    return A2ARequestHandler


def serve_a2a_http(
    *,
    bridge: A2ABridge,
    host: str,
    port: int,
) -> None:
    """Run a blocking A2A HTTP server."""
    server = ThreadingHTTPServer((host, port), build_a2a_handler(bridge))
    try:
        server.serve_forever()
    finally:
        server.server_close()
