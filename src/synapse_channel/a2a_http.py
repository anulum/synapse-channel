# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — stdlib HTTP edge for the Agent2Agent bridge
"""Stdlib HTTP edge for the Agent2Agent bridge."""

from __future__ import annotations

import json
from collections.abc import Iterable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

from synapse_channel import a2a_http_protocol as _protocol
from synapse_channel.a2a import JsonMap
from synapse_channel.a2a_validation import (
    SSE_MEDIA_TYPE,
    TERMINAL_TASK_STATES,
    is_supported_json_media_type,
)
from synapse_channel.core.error_boundaries import http_error_boundary
from synapse_channel.core.protocol import loads_bounded

if TYPE_CHECKING:
    from synapse_channel.a2a_server import A2ABridge

MAX_A2A_JSON_BODY_BYTES = 1024 * 1024

bearer_token_matches = _protocol.bearer_token_matches
non_negative_int = _protocol.non_negative_int
parse_push_config_path = _protocol.parse_push_config_path
problem_response = _protocol.problem_response


def build_a2a_handler(bridge: A2ABridge) -> type[BaseHTTPRequestHandler]:
    """Build a request-handler class bound to ``bridge``.

    Parameters
    ----------
    bridge : A2ABridge
        Bridge orchestrator used by the HTTP edge.

    Returns
    -------
    type[BaseHTTPRequestHandler]
        Configured stdlib HTTP handler class.
    """

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
            media_type: str = _protocol.HTTP_JSON_MEDIA_TYPE,
        ) -> None:
            raw = json.dumps(_protocol.to_wire_json(body), sort_keys=True).encode("utf-8")
            self.send_response(int(status))
            self.send_header("Content-Type", media_type)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _send_sse(self, status: HTTPStatus, body: JsonMap) -> None:
            self._send_sse_events(status, [body])

        def _send_sse_events(self, status: HTTPStatus, bodies: Iterable[JsonMap]) -> None:
            """Send one bounded Server-Sent Events response."""
            raw = b"".join(
                f"data: {json.dumps(_protocol.to_wire_json(body), sort_keys=True)}\n\n".encode()
                for body in bodies
            )
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
                    problem_response(
                        HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                        "Unsupported Media Type",
                        reason="CONTENT_TYPE_NOT_SUPPORTED",
                    ),
                )
                return None
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length > MAX_A2A_JSON_BODY_BYTES:
                self._send_json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    problem_response(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body too large"),
                )
                return None
            raw = self.rfile.read(max(length, 0))
            try:
                data = loads_bounded(raw if raw else "{}")
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    problem_response(HTTPStatus.BAD_REQUEST, "Invalid JSON"),
                )
                return None
            if not isinstance(data, dict):
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    problem_response(HTTPStatus.BAD_REQUEST, "Invalid request body"),
                )
                return None
            return data

        def _send_not_found(self, detail: str = "") -> None:
            reason = "TASK_NOT_FOUND" if detail.startswith("Unknown task:") else None
            self._send_json(
                HTTPStatus.NOT_FOUND,
                problem_response(HTTPStatus.NOT_FOUND, "Not Found", detail, reason=reason),
            )

        def _is_authorized(self) -> bool:
            token = self.bridge.auth_token
            if not token:
                return True
            authorization = self.headers.get("Authorization", "") or ""
            return bearer_token_matches(authorization, token)

        def _require_authorized(self) -> bool:
            if self._is_authorized():
                return True
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                problem_response(HTTPStatus.UNAUTHORIZED, "Unauthorized"),
            )
            return False

        def _negotiate_protocol_version(self, query: str) -> tuple[bool, str | None]:
            """Validate an explicit A2A version and return the active semantics."""
            requested = _protocol.requested_a2a_version(
                self.headers.get("A2A-Version"),
                parse_qs(query).get("A2A-Version", []),
            )
            if not _protocol.supports_a2a_version(requested):
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    problem_response(
                        HTTPStatus.BAD_REQUEST,
                        "Version not supported",
                        f"A2A version {requested!r} is not supported",
                        reason="VERSION_NOT_SUPPORTED",
                        metadata={"requestedVersion": requested or ""},
                    ),
                )
                return False, None
            return True, _protocol.SUPPORTED_A2A_VERSION if requested else None

        def do_GET(self) -> None:
            """Serve A2A discovery and task-read endpoints."""
            parsed = urlparse(self.path)
            if parsed.path == "/.well-known/agent-card.json":
                self._send_json(HTTPStatus.OK, self.bridge.agent_card)
                return
            if not self._require_authorized():
                return
            version_ok, _protocol_version = self._negotiate_protocol_version(parsed.query)
            if not version_ok:
                return
            if parsed.path == "/extendedAgentCard":
                self._send_json(HTTPStatus.OK, self.bridge.agent_card)
                return
            push_path = parse_push_config_path(parsed.path)
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
                        page_size=(non_negative_int(page_size) if page_size is not None else None),
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
                        non_negative_int(history_length) if history_length is not None else None
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
            version_ok, protocol_version = self._negotiate_protocol_version(parsed.query)
            if not version_ok:
                return
            push_path = parse_push_config_path(parsed.path)
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
                        problem_response(
                            HTTPStatus.BAD_REQUEST, "Invalid push notification config"
                        ),
                    )
                    return
                try:
                    created = self.bridge.create_push_notification_config(task_id, config)
                except ValueError as exc:
                    status, title, detail = http_error_boundary(
                        exc, HTTPStatus.BAD_REQUEST, "Invalid push notification config"
                    )
                    self._send_json(
                        status,
                        problem_response(
                            status,
                            title,
                            detail,
                            reason=_protocol.error_info_reason(exc),
                        ),
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
                    streamed = (
                        self.bridge.stream_message(data)
                        if protocol_version is None
                        else self.bridge.stream_message(data, protocol_version=protocol_version)
                    )
                    self._send_sse(
                        HTTPStatus.OK,
                        streamed,
                    )
                except ValueError as exc:
                    status, title, detail = http_error_boundary(
                        exc, HTTPStatus.BAD_REQUEST, "Invalid A2A message"
                    )
                    self._send_json(
                        status,
                        problem_response(
                            status,
                            title,
                            detail,
                            reason=_protocol.error_info_reason(exc),
                        ),
                    )
                return
            if parsed.path == "/message:send":
                data = self._read_json()
                if data is None:
                    return
                try:
                    sent = (
                        self.bridge.send_message(data)
                        if protocol_version is None
                        else self.bridge.send_message(data, protocol_version=protocol_version)
                    )
                    self._send_json(
                        HTTPStatus.OK,
                        sent,
                    )
                except ValueError as exc:
                    status, title, detail = http_error_boundary(
                        exc, HTTPStatus.BAD_REQUEST, "Invalid A2A message"
                    )
                    self._send_json(
                        status,
                        problem_response(
                            status,
                            title,
                            detail,
                            reason=_protocol.error_info_reason(exc),
                        ),
                    )
                return
            if parsed.path in {"/", "/rpc"}:
                data = self._read_json()
                if data is None:
                    return
                result = (
                    self.bridge.handle_json_rpc(data)
                    if protocol_version is None
                    else self.bridge.handle_json_rpc(data, protocol_version=protocol_version)
                )
                self._send_json(
                    HTTPStatus.OK,
                    result,
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
                events = self.bridge.subscribe_task_events(task_id)
                if events is None:
                    self._send_not_found(f"Unknown task: {task_id}")
                    return
                task = events[0]["task"]
                state = str(task.get("status", {}).get("state", ""))
                if state in TERMINAL_TASK_STATES:
                    self._send_json(
                        HTTPStatus.CONFLICT,
                        problem_response(
                            HTTPStatus.CONFLICT,
                            "Task is terminal",
                            "Terminal tasks cannot be subscribed to.",
                            reason="TASK_NOT_CANCELABLE",
                        ),
                    )
                    return
                self._send_sse_events(HTTPStatus.OK, events)
                return
            self._send_not_found()

        def do_DELETE(self) -> None:
            """Serve A2A push-notification config deletion."""
            parsed = urlparse(self.path)
            if not self._require_authorized():
                return
            version_ok, _protocol_version = self._negotiate_protocol_version(parsed.query)
            if not version_ok:
                return
            push_path = parse_push_config_path(parsed.path)
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


def make_a2a_http_server(
    *,
    bridge: A2ABridge,
    host: str,
    port: int,
) -> ThreadingHTTPServer:
    """Build a stdlib A2A HTTP server for callers that manage its lifecycle.

    Parameters
    ----------
    bridge : A2ABridge
        Bridge orchestrator used by the handler.
    host : str
        Host interface to bind.
    port : int
        TCP port to bind.

    Returns
    -------
    ThreadingHTTPServer
        Configured HTTP server.
    """
    return ThreadingHTTPServer((host, port), build_a2a_handler(bridge))


def serve_a2a_http(
    *,
    bridge: A2ABridge,
    host: str,
    port: int,
) -> (
    None
):  # pragma: no cover - blocking process wrapper; server factory is covered by real HTTP tests.
    """Run a blocking A2A HTTP server.

    Parameters
    ----------
    bridge : A2ABridge
        Bridge orchestrator used by the handler.
    host : str
        Host interface to bind.
    port : int
        TCP port to bind.
    """
    server = make_a2a_http_server(bridge=bridge, host=host, port=port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
