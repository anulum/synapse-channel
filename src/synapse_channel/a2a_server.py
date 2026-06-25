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
import uuid
from collections.abc import Callable, Coroutine
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from synapse_channel.a2a import JsonMap
from synapse_channel.client.agent import SynapseAgent

A2A_MEDIA_TYPE = "application/a2a+json"
PROBLEM_MEDIA_TYPE = "application/problem+json"
SSE_MEDIA_TYPE = "text/event-stream"


class A2ATaskStore:
    """In-memory task view for one A2A bridge process."""

    def __init__(self) -> None:
        self._tasks: dict[str, JsonMap] = {}

    def put(self, task: JsonMap) -> JsonMap:
        """Store and return ``task``."""
        self._tasks[str(task["id"])] = task
        return task

    def get(self, task_id: str) -> JsonMap | None:
        """Return one task by id, or ``None``."""
        return self._tasks.get(task_id)

    def list(self, *, state: str | None = None) -> list[JsonMap]:
        """Return tasks, optionally filtered by A2A status state."""
        tasks = list(self._tasks.values())
        if state:
            tasks = [task for task in tasks if task.get("status", {}).get("state") == state]
        return sorted(tasks, key=lambda task: str(task["id"]))


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
    ) -> None:
        self.agent = agent
        self.agent_card = agent_card
        self.target = target
        self.store = store or A2ATaskStore()
        self._submit = submit

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

    def create_completed_task(self, message: JsonMap, *, target: str | None = None) -> JsonMap:
        """Create a completed A2A task for a delivered SYNAPSE message."""
        task_id = str(message.get("taskId") or uuid.uuid4())
        context_id = str(message.get("contextId") or uuid.uuid4())
        resolved_target = self._target_for(message, target)
        text = self._message_text(message)
        task = {
            "id": task_id,
            "contextId": context_id,
            "status": {
                "state": "TASK_STATE_COMPLETED",
                "message": {
                    "messageId": str(uuid.uuid4()),
                    "role": "ROLE_AGENT",
                    "parts": [
                        {
                            "text": f"Delivered to SYNAPSE target {resolved_target}.",
                            "mediaType": "text/plain",
                        }
                    ],
                },
            },
            "history": [message],
            "artifacts": [
                {
                    "artifactId": "synapse-delivery",
                    "name": "SYNAPSE delivery receipt",
                    "description": "Local bridge delivery receipt.",
                    "parts": [
                        {
                            "data": {"target": resolved_target, "delivered": True},
                            "mediaType": "application/json",
                        }
                    ],
                }
            ],
            "metadata": {"synapseTarget": resolved_target},
        }
        if text:
            self._run(self.agent.chat(text, target=resolved_target))
        return self.store.put(task)

    def send_message(self, payload: JsonMap) -> JsonMap:
        """Handle an A2A ``message:send`` request."""
        return {"task": self._send_message_task(payload)}

    def stream_message(self, payload: JsonMap) -> JsonMap:
        """Handle an A2A ``message:stream`` request as an immediate lifecycle stream."""
        return {"task": self._send_message_task(payload)}

    def _send_message_task(self, payload: JsonMap) -> JsonMap:
        """Validate a send payload and return the created task."""
        message = payload.get("message")
        if not isinstance(message, dict):
            raise ValueError("message must be an object")
        if not message.get("messageId"):
            raise ValueError("message.messageId is required")
        if message.get("role") != "ROLE_USER":
            raise ValueError("message.role must be ROLE_USER")
        parts = message.get("parts")
        if not isinstance(parts, list) or not parts:
            raise ValueError("message.parts must be a non-empty array")
        return self.create_completed_task(message)

    def list_tasks(self, *, state: str | None = None) -> JsonMap:
        """Return an A2A task-list response."""
        tasks = self.store.list(state=state)
        return {
            "tasks": tasks,
            "nextPageToken": "",
            "pageSize": len(tasks),
            "totalSize": len(tasks),
        }

    def get_task(self, task_id: str) -> JsonMap | None:
        """Return one A2A task by id."""
        return self.store.get(task_id)

    def cancel_task(self, task_id: str) -> JsonMap | None:
        """Cancel a stored A2A task."""
        task = self.store.get(task_id)
        if task is None:
            return None
        task["status"] = {"state": "TASK_STATE_CANCELED"}
        self.store.put(task)
        return task

    def subscribe_task(self, task_id: str) -> JsonMap | None:
        """Return a task snapshot for SSE subscription, or ``None`` when unknown."""
        return self.store.get(task_id)


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
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
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

        def do_GET(self) -> None:
            """Serve A2A discovery and task-read endpoints."""
            parsed = urlparse(self.path)
            if parsed.path in {"/.well-known/agent-card.json", "/extendedAgentCard"}:
                self._send_json(HTTPStatus.OK, self.bridge.agent_card)
                return
            if parsed.path == "/tasks":
                query = parse_qs(parsed.query)
                state = query.get("status", [None])[0]
                self._send_json(HTTPStatus.OK, self.bridge.list_tasks(state=state))
                return
            if parsed.path.startswith("/tasks/"):
                task_id = parsed.path.removeprefix("/tasks/")
                if ":" in task_id:
                    self._send_not_found()
                    return
                task = self.bridge.get_task(task_id)
                if task is None:
                    self._send_not_found(f"Unknown task: {task_id}")
                    return
                self._send_json(HTTPStatus.OK, task)
                return
            self._send_not_found()

        def do_POST(self) -> None:
            """Serve A2A message-send and task-cancel endpoints."""
            parsed = urlparse(self.path)
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
                task = self.bridge.subscribe_task(task_id)
                if task is None:
                    self._send_not_found(f"Unknown task: {task_id}")
                    return
                state = str(task.get("status", {}).get("state", ""))
                if state in {
                    "TASK_STATE_COMPLETED",
                    "TASK_STATE_FAILED",
                    "TASK_STATE_CANCELED",
                    "TASK_STATE_REJECTED",
                }:
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
                self._send_sse(HTTPStatus.OK, {"task": task})
                return
            self._send_not_found()

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
