# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — admitted HTTP routes for the Agent2Agent bridge
"""Dispatch admitted A2A HTTP requests without combining transport and task logic."""

from __future__ import annotations

from http import HTTPStatus
from urllib.parse import parse_qs, urlparse

from synapse_channel import a2a_http_protocol as _protocol
from synapse_channel.a2a_http_handler import A2AHTTPHandler
from synapse_channel.a2a_validation import TERMINAL_TASK_STATES
from synapse_channel.core.error_boundaries import http_error_boundary

non_negative_int = _protocol.non_negative_int
parse_push_config_path = _protocol.parse_push_config_path
parse_push_delivery_path = _protocol.parse_push_delivery_path
problem_response = _protocol.problem_response


class A2ARouteHandler(A2AHTTPHandler):
    """Route admitted task, message and push-notification HTTP requests."""

    def do_GET(self) -> None:
        """Serve discovery before auth, then negotiated task-read routes."""
        if not self._require_browser_boundary():
            return
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
        delivery_task = parse_push_delivery_path(parsed.path)
        if delivery_task is not None:
            self._get_push_deliveries(delivery_task)
            return
        push_path = parse_push_config_path(parsed.path)
        if push_path is not None:
            self._get_push_config(*push_path)
            return
        if parsed.path == "/tasks":
            self._get_tasks(parsed.query)
            return
        if parsed.path.startswith("/tasks/"):
            self._get_task(parsed.path, parsed.query)
            return
        self._send_not_found()

    def do_POST(self) -> None:
        """Admit writes before dispatching their existing task/message stages."""
        if not self._require_browser_boundary():
            return
        parsed = urlparse(self.path)
        if not self._require_authorized():
            return
        version_ok, protocol_version = self._negotiate_protocol_version(parsed.query)
        if not version_ok:
            return
        push_path = parse_push_config_path(parsed.path)
        if push_path is not None:
            self._post_push_config(*push_path)
        elif parsed.path == "/message:stream":
            self._post_stream(protocol_version)
        elif parsed.path == "/message:send":
            self._post_message(protocol_version)
        elif parsed.path in {"/", "/rpc"}:
            self._post_rpc(protocol_version)
        elif parsed.path.startswith("/tasks/") and parsed.path.endswith(":cancel"):
            self._post_cancel(parsed.path)
        elif parsed.path.startswith("/tasks/") and parsed.path.endswith(":subscribe"):
            self._post_subscription(parsed.path)
        else:
            self._send_not_found()

    def _get_push_deliveries(self, task_id: str) -> None:
        """Serve delivery history only after proving the task exists."""
        if self.bridge.get_task(task_id) is None:
            self._send_not_found(f"Unknown task: {task_id}")
            return
        deliveries = self.bridge.list_push_notification_deliveries(task_id)
        self._send_json(HTTPStatus.OK, deliveries)
        return

    def _get_push_config(self, task_id: str, config_id: str | None) -> None:
        """Serve one push configuration or its task-scoped list."""
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

    def _get_tasks(self, query_text: str) -> None:
        """Serve the paginated task list with unchanged query coercion."""
        query = parse_qs(query_text)
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

    def _get_task(self, path: str, query_text: str) -> None:
        """Serve one task and its requested history length."""
        task_id = path.removeprefix("/tasks/")
        if ":" in task_id:
            self._send_not_found()
            return
        query = parse_qs(query_text)
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

    def _post_push_config(self, task_id: str, config_id: str | None) -> None:
        """Create a validated task-scoped push configuration."""
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
                problem_response(HTTPStatus.BAD_REQUEST, "Invalid push notification config"),
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

    def _post_stream(self, protocol_version: str | None) -> None:
        """Stream one validated message using the negotiated semantics."""
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

    def _post_message(self, protocol_version: str | None) -> None:
        """Send one validated message using the negotiated semantics."""
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

    def _post_rpc(self, protocol_version: str | None) -> None:
        """Dispatch a validated JSON-RPC body using negotiated semantics."""
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

    def _post_cancel(self, path: str) -> None:
        """Cancel an existing task with its original not-found response."""
        task_id = path.removeprefix("/tasks/").removesuffix(":cancel")
        task = self.bridge.cancel_task(task_id)
        if task is None:
            self._send_not_found(f"Unknown task: {task_id}")
            return
        self._send_json(HTTPStatus.OK, task)
        return

    def _post_subscription(self, path: str) -> None:
        """Serve durable task events only for a non-terminal task."""
        task_id = path.removeprefix("/tasks/").removesuffix(":subscribe")
        events = self.bridge.subscribe_task_events(task_id)
        if events is None:
            self._send_not_found(f"Unknown task: {task_id}")
            return
        # Durable history may start with non-terminal WORKING events; the current
        # (final) snapshot decides whether the task is terminal.
        task = events[-1]["task"]
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

    def do_DELETE(self) -> None:
        """Serve A2A push-notification config deletion."""
        if not self._require_browser_boundary():
            return
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
